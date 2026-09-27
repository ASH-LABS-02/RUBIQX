import 'dart:async';
import 'package:flutter/material.dart';
import 'screens/home_screen.dart';
import 'services/alert_service.dart';
import 'services/alerts_controller.dart';
import 'services/connection_manager.dart';
import 'services/location_service.dart';
import 'services/notification_service.dart';
import 'services/settings_service.dart';
import 'theme/app_theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final settings = await SettingsService.create();
  final connection = ConnectionManager(settings);
  final alertService = AlertService(connection);
  final notifications = NotificationService();
  await notifications.init();
  await notifications.requestPermission();

  final alertsController = AlertsController(
    alertService: alertService,
    connection: connection,
    notifications: notifications,
    soundEnabled: () => settings.soundEnabled,
  );
  final locationService = LocationService(connection, settings);

  connection.start();
  alertsController.start();
  // Best-effort: a rescuer who denies the permission prompt still gets a
  // fully working app, just without their own dot on the "nearest team"
  // distance -- see LocationService's own comment on why this can't fail
  // the whole app.
  unawaited(locationService.start());

  runApp(SarRescueApp(
    connection: connection,
    alertService: alertService,
    alertsController: alertsController,
    locationService: locationService,
    settings: settings,
    notifications: notifications,
  ));
}

class SarRescueApp extends StatelessWidget {
  final ConnectionManager connection;
  final AlertService alertService;
  final AlertsController alertsController;
  final LocationService locationService;
  final SettingsService settings;
  final NotificationService notifications;

  const SarRescueApp({
    super.key,
    required this.connection,
    required this.alertService,
    required this.alertsController,
    required this.locationService,
    required this.settings,
    required this.notifications,
  });

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'SAR Rescue',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      home: HomeScreen(
        controller: alertsController,
        alertService: alertService,
        connection: connection,
        locationService: locationService,
        settings: settings,
        notifications: notifications,
      ),
    );
  }
}
