import 'package:flutter/material.dart';
import '../services/alert_service.dart';
import '../services/alerts_controller.dart';
import '../services/connection_manager.dart';
import '../services/location_service.dart';
import '../services/notification_service.dart';
import '../services/settings_service.dart';
import '../widgets/device_chrome.dart';
import 'alerts_screen.dart';
import 'live_feed_screen.dart';
import 'map_screen.dart';
import 'settings_screen.dart';
import 'stats_screen.dart';

class HomeScreen extends StatefulWidget {
  final AlertsController controller;
  final AlertService alertService;
  final ConnectionManager connection;
  final LocationService locationService;
  final SettingsService settings;
  final NotificationService notifications;

  const HomeScreen({
    super.key,
    required this.controller,
    required this.alertService,
    required this.connection,
    required this.locationService,
    required this.settings,
    required this.notifications,
  });

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final screens = [
      AlertsScreen(
        controller: widget.controller,
        alertService: widget.alertService,
        connection: widget.connection,
      ),
      MapScreen(
        controller: widget.controller,
        alertService: widget.alertService,
        connection: widget.connection,
      ),
      LiveFeedScreen(
        alertService: widget.alertService,
        connection: widget.connection,
      ),
      StatsScreen(controller: widget.controller, connection: widget.connection),
      SettingsScreen(
        connection: widget.connection,
        locationService: widget.locationService,
        settings: widget.settings,
        notifications: widget.notifications,
      ),
    ];

    return Scaffold(
      body: Column(
        children: [
          DeviceHeader(
            connection: widget.connection,
            controller: widget.controller,
          ),
          Expanded(
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 220),
              switchInCurve: Curves.easeOut,
              switchOutCurve: Curves.easeIn,
              transitionBuilder: (child, animation) =>
                  FadeTransition(opacity: animation, child: child),
              child: KeyedSubtree(
                key: ValueKey(_index),
                child: screens[_index],
              ),
            ),
          ),
        ],
      ),
      bottomNavigationBar: DeviceNavBar(
        selectedIndex: _index,
        onSelect: (i) => setState(() => _index = i),
        items: const [
          DeviceNavItem(
            icon: Icons.warning_amber_rounded,
            selectedIcon: Icons.warning_rounded,
            label: 'Alerts',
          ),
          DeviceNavItem(
            icon: Icons.map_outlined,
            selectedIcon: Icons.map_rounded,
            label: 'Map',
          ),
          DeviceNavItem(
            icon: Icons.videocam_outlined,
            selectedIcon: Icons.videocam_rounded,
            label: 'Live',
          ),
          DeviceNavItem(
            icon: Icons.insights_outlined,
            selectedIcon: Icons.insights_rounded,
            label: 'Stats',
          ),
          DeviceNavItem(
            icon: Icons.settings_outlined,
            selectedIcon: Icons.settings_rounded,
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}
