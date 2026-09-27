import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import '../models/alert.dart';
import '../theme/app_theme.dart' show AppColors;

/// Turns a new Alert into an actual attention-grabbing notification --
/// sound, vibration, heads-up banner -- while the app is running.
///
/// This is local notifications, not push: it fires because the app is
/// alive and its SSE connection (AlertService.liveStream) just delivered
/// an event, not because a server woke the phone up from fully killed.
/// That is a deliberate, honest scope for this prototype -- it works the
/// moment the app is open or backgrounded-but-not-killed, with zero cloud
/// setup. True wake-from-killed push needs Firebase Cloud Messaging (a
/// Firebase project + google-services.json, which needs the team's own
/// Google account to create) or an Android foreground service; either is a
/// reasonable next step but neither is built here.
class NotificationService {
  static const _channelId = 'sar_emergency_alerts';
  static const _channelName = 'Emergency Alerts';
  static const _channelDescription =
      'A human was detected by the drone or ground camera';

  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _initialized = false;

  Future<void> init() async {
    if (_initialized) return;

    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    const iosInit = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    await _plugin.initialize(
      settings: const InitializationSettings(android: androidInit, iOS: iosInit),
    );

    // High-importance channel: heads-up banner + sound + vibration. An
    // emergency detection that arrives as a silent, easy-to-miss
    // notification defeats the purpose of the feature.
    const androidChannel = AndroidNotificationChannel(
      _channelId,
      _channelName,
      description: _channelDescription,
      importance: Importance.max,
      playSound: true,
      enableVibration: true,
      vibrationPattern: null, // use the OS default emergency-style pattern
    );
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(androidChannel);

    _initialized = true;
  }

  Future<bool> requestPermission() async {
    final androidGranted = await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    final iosGranted = await _plugin
        .resolvePlatformSpecificImplementation<
            IOSFlutterLocalNotificationsPlugin>()
        ?.requestPermissions(alert: true, badge: true, sound: true);
    return (androidGranted ?? true) && (iosGranted ?? true);
  }

  Future<void> notifyAlert(Alert alert) async {
    if (!_initialized) await init();

    final locationText = alert.hasLocation
        ? '${alert.lat!.toStringAsFixed(5)}, ${alert.lon!.toStringAsFixed(5)}'
        : 'location unavailable';
    final routeText = alert.isFromMesh
        ? ' via LoRa mesh (${alert.hopsOrZero} hop${alert.hopsOrZero == 1 ? '' : 's'})'
        : '';

    await _plugin.show(
      id: alert.id.hashCode,
      title: '⚠ HUMAN DETECTED',
      body: '${alert.personCount} person(s), ${alert.confidence.toStringAsFixed(0)}% '
          'confidence — $locationText$routeText',
      notificationDetails: const NotificationDetails(
        android: AndroidNotificationDetails(
          _channelId,
          _channelName,
          channelDescription: _channelDescription,
          importance: Importance.max,
          priority: Priority.high,
          category: AndroidNotificationCategory.alarm,
          fullScreenIntent: true,
          color: AppColors.red,
        ),
        iOS: DarwinNotificationDetails(
          interruptionLevel: InterruptionLevel.timeSensitive,
        ),
      ),
    );
  }
}
