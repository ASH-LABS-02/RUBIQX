import 'package:shared_preferences/shared_preferences.dart';

/// Persisted, user-editable configuration. Kept deliberately small and
/// explicit -- a rescue-team member configures this once (or accepts the
/// default) and it survives app restarts.
class SettingsService {
  static const _keyLocalUrl = 'gcs_local_url';
  static const _keyCloudUrl = 'gcs_cloud_url';
  static const _keyRescuerName = 'rescuer_name';
  static const _keySoundEnabled = 'alert_sound_enabled';

  /// The Ground Station Pi's address on the local network or its own
  /// hotspot. This is what makes the app work with zero internet -- see
  /// ConnectionMode.local. Defaults to the Pi's address on the bench
  /// network this project has been developed against; change it in
  /// Settings to match wherever the GCS actually is.
  static const defaultLocalUrl = 'http://192.168.1.9:5000';

  final SharedPreferences _prefs;
  SettingsService(this._prefs);

  static Future<SettingsService> create() async {
    return SettingsService(await SharedPreferences.getInstance());
  }

  String get localUrl => _prefs.getString(_keyLocalUrl) ?? defaultLocalUrl;
  Future<void> setLocalUrl(String url) => _prefs.setString(_keyLocalUrl, _normalize(url));

  /// Empty until a rescuer configures a real relay. The app works fully
  /// without one -- see ConnectionManager.
  String get cloudUrl => _prefs.getString(_keyCloudUrl) ?? '';
  Future<void> setCloudUrl(String url) => _prefs.setString(_keyCloudUrl, _normalize(url));

  String get rescuerName => _prefs.getString(_keyRescuerName) ?? '';
  Future<void> setRescuerName(String name) => _prefs.setString(_keyRescuerName, name.trim());

  bool get soundEnabled => _prefs.getBool(_keySoundEnabled) ?? true;
  Future<void> setSoundEnabled(bool value) => _prefs.setBool(_keySoundEnabled, value);

  static String _normalize(String url) {
    final trimmed = url.trim();
    if (trimmed.isEmpty) return trimmed;
    final withScheme = trimmed.contains('://') ? trimmed : 'http://$trimmed';
    return withScheme.endsWith('/')
        ? withScheme.substring(0, withScheme.length - 1)
        : withScheme;
  }
}
