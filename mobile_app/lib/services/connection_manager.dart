import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/connection_mode.dart';
import 'settings_service.dart';

/// Decides, right now, how to reach the Ground Control Station -- and
/// re-decides continuously as networks come and go.
///
/// The policy is deliberately local-first: a local/hotspot link to the GCS
/// Pi is lower latency, has no dependency on any internet infrastructure
/// (which may simply not exist in a disaster area), and is the freshest
/// possible data since nothing relays it. Cloud is only consulted if local
/// is unreachable and a cloud URL has actually been configured -- this
/// prototype ships with no cloud relay built, so by default the app is
/// local-only and that is a complete, working, offline-first system on its
/// own. See ConnectionMode for the physical reasoning (a phone has no LoRa
/// radio; the GCS Pi is always the real endpoint, only the last hop to it
/// changes).
class ConnectionManager extends ChangeNotifier {
  final SettingsService settings;
  static const _probeTimeout = Duration(seconds: 3);
  static const _pollInterval = Duration(seconds: 10);

  ConnectionMode _mode = ConnectionMode.offline;
  DateTime? _lastSuccessAt;
  Timer? _pollTimer;
  bool _probing = false;

  ConnectionManager(this.settings);

  ConnectionMode get mode => _mode;
  DateTime? get lastSuccessAt => _lastSuccessAt;

  /// The base URL to use for every API call right now. Null only when
  /// truly offline -- callers should fall back to cached data.
  String? get activeBaseUrl => switch (_mode) {
        ConnectionMode.local => settings.localUrl,
        ConnectionMode.cloud => settings.cloudUrl,
        ConnectionMode.offline => null,
      };

  void start() {
    unawaited(probeNow());
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(_pollInterval, (_) => probeNow());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  /// Re-check reachability immediately. Safe to call from a pull-to-refresh
  /// or a "retry" button in addition to the periodic timer.
  Future<void> probeNow() async {
    if (_probing) return;
    _probing = true;
    try {
      if (await _reachable(settings.localUrl)) {
        _setMode(ConnectionMode.local);
        return;
      }
      if (settings.cloudUrl.isNotEmpty && await _reachable(settings.cloudUrl)) {
        _setMode(ConnectionMode.cloud);
        return;
      }
      _setMode(ConnectionMode.offline);
    } finally {
      _probing = false;
    }
  }

  Future<bool> _reachable(String baseUrl) async {
    if (baseUrl.isEmpty) return false;
    try {
      final res = await http
          .get(Uri.parse('$baseUrl/api/health'))
          .timeout(_probeTimeout);
      if (res.statusCode != 200) return false;
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      return body['status'] == 'ok';
    } catch (_) {
      return false;
    }
  }

  void _setMode(ConnectionMode newMode) {
    if (newMode != ConnectionMode.offline) {
      _lastSuccessAt = DateTime.now();
    }
    if (newMode != _mode) {
      _mode = newMode;
      notifyListeners();
    }
  }
}
