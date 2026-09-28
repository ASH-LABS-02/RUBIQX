import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'connection_manager.dart';
import 'settings_service.dart';

/// Reports this rescuer's own phone position to the GCS Pi's
/// `/api/team/location` on a fixed timer while the app is in the
/// foreground -- powers the dashboard's "nearest rescue team" distance
/// (see api_team_nearest() in Drone-model/app.py). Foreground-only,
/// deliberately: see AndroidManifest.xml's comment on why this doesn't
/// use ACCESS_BACKGROUND_LOCATION.
///
/// Uses a plain timer + getCurrentPosition() rather than
/// Geolocator's position *stream* -- less battery-efficient, but far
/// easier to reason about and test than a stream whose emission cadence
/// depends on the OS's own location provider, and this app's other
/// "phone -> GCS" traffic (none yet) has no reason to need finer-grained
/// updates than one fix every [_reportInterval].
class LocationService extends ChangeNotifier {
  final ConnectionManager connection;
  final SettingsService settings;
  static const _reportInterval = Duration(seconds: 15);
  static const _keyMemberId = 'team_member_id';

  Timer? _timer;
  String? _memberId;
  Position? _lastPosition;
  String? _lastError;
  bool _sharing = false;

  LocationService(this.connection, this.settings);

  bool get isSharing => _sharing;
  Position? get lastPosition => _lastPosition;
  String? get lastError => _lastError;

  /// Requests location permission (if not already granted) and, if
  /// granted, starts reporting. Safe to call more than once -- e.g. after
  /// the user grants permission from a settings prompt following an
  /// earlier denial.
  Future<bool> start() async {
    _memberId ??= await _loadOrCreateMemberId();

    if (!await _ensurePermission()) {
      _lastError = 'Location permission denied';
      _sharing = false;
      notifyListeners();
      return false;
    }

    _sharing = true;
    _lastError = null;
    notifyListeners();
    unawaited(_reportOnce());
    _timer?.cancel();
    _timer = Timer.periodic(_reportInterval, (_) => _reportOnce());
    return true;
  }

  void stop() {
    _timer?.cancel();
    _sharing = false;
    notifyListeners();
  }

  Future<bool> _ensurePermission() async {
    if (!await Geolocator.isLocationServiceEnabled()) return false;
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    return permission == LocationPermission.always || permission == LocationPermission.whileInUse;
  }

  Future<void> _reportOnce() async {
    final base = connection.activeBaseUrl;
    if (base == null) return; // no GCS reachable right now -- next tick tries again

    try {
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(const Duration(seconds: 10));
      _lastPosition = position;
      _lastError = null;

      await http
          .post(
            Uri.parse('$base/api/team/location'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'member_id': _memberId,
              'name': settings.rescuerName,
              'lat': position.latitude,
              'lon': position.longitude,
              'accuracy': position.accuracy,
              'timestamp': position.timestamp.millisecondsSinceEpoch,
            }),
          )
          .timeout(const Duration(seconds: 8));
    } catch (e) {
      // Best-effort: a missed report just means the dashboard's distance
      // figure doesn't update this tick, not that the app should crash or
      // stop trying -- the next timer tick tries again.
      _lastError = e.toString();
    }
    notifyListeners();
  }

  Future<String> _loadOrCreateMemberId() async {
    final prefs = await SharedPreferences.getInstance();
    final existing = prefs.getString(_keyMemberId);
    if (existing != null) return existing;
    final generated = _randomId();
    await prefs.setString(_keyMemberId, generated);
    return generated;
  }

  /// A stable per-install identifier -- not a real UUID (no package for
  /// one is already a dependency here), just random enough that two
  /// installs never collide in practice for a handful of rescue-team
  /// phones.
  String _randomId() {
    final rand = Random();
    return List.generate(12, (_) => rand.nextInt(16).toRadixString(16)).join();
  }
}
