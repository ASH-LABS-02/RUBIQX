// Basic smoke test: the app builds and shows its bottom navigation without
// throwing. Deliberately not deeper than that -- the real screens depend on
// live network services (ConnectionManager, AlertService) that need a
// running gateway to test meaningfully; that's what tools/checkradio.py-style
// manual verification against the actual Pi is for, not a widget test.

import 'package:flutter_test/flutter_test.dart';
import 'package:sar_rescue_app/main.dart';
import 'package:sar_rescue_app/services/alert_service.dart';
import 'package:sar_rescue_app/services/alerts_controller.dart';
import 'package:sar_rescue_app/services/connection_manager.dart';
import 'package:sar_rescue_app/services/location_service.dart';
import 'package:sar_rescue_app/services/notification_service.dart';
import 'package:sar_rescue_app/services/settings_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('App shell renders with bottom navigation', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final settings = await SettingsService.create();
    final connection = ConnectionManager(settings);
    final alertService = AlertService(connection);
    final notifications = NotificationService();
    final controller = AlertsController(
      alertService: alertService,
      connection: connection,
      notifications: notifications,
      soundEnabled: () => settings.soundEnabled,
    );
    final locationService = LocationService(connection, settings);

    await tester.pumpWidget(SarRescueApp(
      connection: connection,
      alertService: alertService,
      alertsController: controller,
      locationService: locationService,
      settings: settings,
      notifications: notifications,
    ));
    await tester.pump();

    expect(find.text('ALERTS'), findsWidgets);
    expect(find.text('MAP'), findsOneWidget);
    expect(find.text('LIVE'), findsOneWidget);
    expect(find.text('STATS'), findsOneWidget);
    expect(find.text('SETTINGS'), findsOneWidget);
  });
}
