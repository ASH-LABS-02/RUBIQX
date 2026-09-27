import 'dart:async';
import 'package:flutter/foundation.dart';
import '../models/alert.dart';
import '../models/connection_mode.dart';
import 'alert_service.dart';
import 'connection_manager.dart';
import 'notification_service.dart';

/// Owns the in-memory alert list for the whole app: an initial REST fetch,
/// then a live SSE subscription that keeps it current and fires a local
/// notification for every genuinely new alert.
///
/// The live stream is not trusted as the only notification path. In
/// practice it reconnects often -- a phone's radio gets throttled in the
/// background, a WiFi hiccup, the dev server under load -- and any alert
/// created during one of those gaps would otherwise never notify at all,
/// even though it correctly shows up in the list on the next fetch. Every
/// refresh() (periodic, and triggered on every reconnect) is therefore
/// itself a notification path: anything present on the server that this
/// phone hasn't been notified about yet gets notified now, whichever way
/// it was discovered. Delayed-but-guaranteed beats instant-but-lossy for
/// an emergency alert.
class AlertsController extends ChangeNotifier {
  final AlertService alertService;
  final ConnectionManager connection;
  final NotificationService notifications;
  final bool Function() soundEnabled;

  AlertsController({
    required this.alertService,
    required this.connection,
    required this.notifications,
    required this.soundEnabled,
  });

  static const _periodicRefresh = Duration(seconds: 20);

  final List<Alert> _alerts = [];
  List<Alert> get alerts => List.unmodifiable(_alerts);

  bool _loading = false;
  bool get loading => _loading;
  String? _lastError;
  String? get lastError => _lastError;

  StreamSubscription<Alert>? _liveSub;
  ConnectionMode? _lastMode;
  Timer? _periodicTimer;

  /// Every alert id this phone has already raised a notification for --
  /// live or caught up via refresh -- so neither path notifies twice.
  final Set<String> _notifiedIds = {};
  bool _hasLoadedOnce = false;

  int get newCount => _alerts.where((a) => a.isNew).length;

  void start() {
    connection.addListener(_onConnectionChanged);
    unawaited(refresh());
    _subscribeLive();
    // The live stream is the fast path, not the only path -- this timer is
    // what guarantees an alert eventually notifies even if the stream has
    // been silently dead the whole time (see the class doc above).
    _periodicTimer = Timer.periodic(_periodicRefresh, (_) => refresh());
  }

  @override
  void dispose() {
    connection.removeListener(_onConnectionChanged);
    _liveSub?.cancel();
    _periodicTimer?.cancel();
    super.dispose();
  }

  void _onConnectionChanged() {
    // Reconnecting the SSE stream is handled inside AlertService.liveStream
    // itself; what changes here is worth re-fetching once we regain any
    // connection at all, in case events were missed entirely while offline.
    if (connection.mode != _lastMode) {
      _lastMode = connection.mode;
      if (connection.mode != ConnectionMode.offline) {
        unawaited(refresh());
      }
      notifyListeners(); // let UI reflect connectivity-dependent state (media URLs etc.)
    }
  }

  Future<void> refresh() async {
    _loading = true;
    notifyListeners();
    try {
      final fetched = await alertService.fetchAlerts();

      // First load ever (app cold start, possibly with months of history
      // in alerts.jsonl on the backend): mark everything as already seen
      // without notifying -- a rescuer opening the app should not get 200
      // notifications for detections that already happened.
      if (!_hasLoadedOnce) {
        _hasLoadedOnce = true;
        _notifiedIds.addAll(fetched.map((a) => a.id));
      } else {
        // Every subsequent refresh: anything present now that we have not
        // already notified about (live or via an earlier refresh) is
        // exactly what the live stream missed -- notify for it now. Oldest
        // first, so notifications land in the order things actually
        // happened rather than newest-first.
        final missed = fetched.where((a) => !_notifiedIds.contains(a.id)).toList()
          ..sort((a, b) => a.createdAt.compareTo(b.createdAt));
        for (final alert in missed) {
          _notifiedIds.add(alert.id);
          unawaited(notifications.notifyAlert(alert));
        }
      }

      _alerts
        ..clear()
        ..addAll(fetched);
      _lastError = null;
    } catch (e) {
      // Keep whatever we already have cached -- a failed refresh should
      // never blank the screen a rescuer is relying on.
      _lastError = e.toString();
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  void _subscribeLive() {
    _liveSub?.cancel();
    _liveSub = alertService.liveStream().listen(_onLiveAlert);
  }

  void _onLiveAlert(Alert incoming) {
    final idx = _alerts.indexWhere((a) => a.id == incoming.id);
    final isTrulyNew = idx == -1;

    if (isTrulyNew) {
      _alerts.insert(0, incoming);
    } else {
      _alerts[idx] = incoming; // status update republish
    }
    notifyListeners();

    // Guard against the live push and a concurrent/just-missed refresh()
    // both noticing the same brand-new alert -- whichever gets here first
    // claims it, the other sees it already in _notifiedIds and skips.
    if (isTrulyNew && _notifiedIds.add(incoming.id)) {
      unawaited(notifications.notifyAlert(incoming));
    }
  }

  Future<void> updateStatus(String alertId, String status, String? by) async {
    final updated = await alertService.updateStatus(alertId, status, by);
    final idx = _alerts.indexWhere((a) => a.id == alertId);
    if (idx != -1) {
      _alerts[idx] = updated;
      notifyListeners();
    }
  }
}
