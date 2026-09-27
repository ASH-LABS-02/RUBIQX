import 'package:flutter/material.dart';
import '../models/alert.dart';
import '../models/connection_mode.dart';
import '../services/connection_manager.dart';
import '../services/location_service.dart';
import '../services/notification_service.dart';
import '../services/settings_service.dart';
import '../theme/app_theme.dart';

class SettingsScreen extends StatefulWidget {
  final ConnectionManager connection;
  final LocationService locationService;
  final SettingsService settings;
  final NotificationService notifications;
  const SettingsScreen({
    super.key,
    required this.connection,
    required this.locationService,
    required this.settings,
    required this.notifications,
  });

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _localUrlCtrl;
  late final TextEditingController _cloudUrlCtrl;
  late final TextEditingController _nameCtrl;
  late bool _soundEnabled;

  @override
  void initState() {
    super.initState();
    final s = widget.connection.settings;
    _localUrlCtrl = TextEditingController(text: s.localUrl);
    _cloudUrlCtrl = TextEditingController(text: s.cloudUrl);
    _nameCtrl = TextEditingController(text: s.rescuerName);
    _soundEnabled = widget.settings.soundEnabled;
  }

  Future<void> _sendTestNotification() async {
    await widget.notifications.notifyAlert(
      Alert(
        id: 'test-${DateTime.now().millisecondsSinceEpoch}',
        createdAt: DateTime.now().millisecondsSinceEpoch / 1000,
        confidence: 91.0,
        personCount: 1,
        source: 'camera-direct',
      ),
    );
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'Test notification sent — check your notification shade',
          ),
        ),
      );
    }
  }

  @override
  void dispose() {
    _localUrlCtrl.dispose();
    _cloudUrlCtrl.dispose();
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final s = widget.connection.settings;
    await s.setLocalUrl(_localUrlCtrl.text);
    await s.setCloudUrl(_cloudUrlCtrl.text);
    await s.setRescuerName(_nameCtrl.text);
    await widget.connection.probeNow();
    if (mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Settings saved')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
      children: [
        const Padding(
          padding: EdgeInsets.only(bottom: 14),
          child: Text(
            'TERMINAL CONFIGURATION',
            style: TextStyle(
              fontFamily: AppFonts.data,
              fontSize: 11,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.2,
              color: AppColors.dim,
            ),
          ),
        ),
        _SettingsGroup(
          title: 'Rescuer',
          icon: Icons.badge_outlined,
          children: [
            TextField(
              controller: _nameCtrl,
              decoration: const InputDecoration(
                labelText: 'Your name',
                hintText: 'Shown when you dispatch or resolve an alert',
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        _LocationSharingGroup(locationService: widget.locationService),
        const SizedBox(height: 16),
        _SettingsGroup(
          title: 'Alerts',
          icon: Icons.notifications_outlined,
          iconColor: AppColors.amber,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Alert sound',
                    style: const TextStyle(
                      fontFamily: AppFonts.body,
                      fontSize: 13.5,
                      color: AppColors.text,
                    ),
                  ),
                ),
                Switch(
                  value: _soundEnabled,
                  activeThumbColor: AppColors.cyan,
                  onChanged: (v) async {
                    setState(() => _soundEnabled = v);
                    await widget.settings.setSoundEnabled(v);
                  },
                ),
              ],
            ),
            const SizedBox(height: 6),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: _sendTestNotification,
                icon: const Icon(Icons.campaign_outlined, size: 18),
                label: const Text('Send test notification'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        _SettingsGroup(
          title: 'Ground Station — Local',
          icon: Icons.wifi_tethering_rounded,
          iconColor: AppColors.green,
          subtitle:
              'The Ground Station Pi\'s address on its own WiFi hotspot or a '
              'shared local network. This is how alerts and video reach the app '
              'with zero internet — the Pi is what actually receives the LoRa '
              'mesh; the phone reaches the Pi over this local link.',
          children: [
            TextField(
              controller: _localUrlCtrl,
              decoration: const InputDecoration(
                labelText: 'GCS local address',
                hintText: 'http://192.168.1.9:5000',
              ),
              keyboardType: TextInputType.url,
            ),
          ],
        ),
        const SizedBox(height: 16),
        _SettingsGroup(
          title: 'Cloud Relay — Optional',
          icon: Icons.cloud_outlined,
          iconColor: AppColors.cyan,
          subtitle:
              'Only used when the local link above is unreachable. Leave blank '
              'unless a cloud relay has actually been deployed — this prototype '
              'does not ship one; the app works fully on local alone.',
          children: [
            TextField(
              controller: _cloudUrlCtrl,
              decoration: const InputDecoration(
                labelText: 'Cloud relay address (optional)',
                hintText: 'https://your-relay.example.com',
              ),
              keyboardType: TextInputType.url,
            ),
          ],
        ),
        const SizedBox(height: 22),
        SizedBox(
          width: double.infinity,
          child: FilledButton(
            onPressed: _save,
            child: const Text('Save & Reconnect'),
          ),
        ),
        const SizedBox(height: 24),
        _StatusCard(connection: widget.connection),
      ],
    );
  }
}

class _SettingsGroup extends StatelessWidget {
  final String title;
  final IconData icon;
  final Color? iconColor;
  final String? subtitle;
  final List<Widget> children;

  const _SettingsGroup({
    required this.title,
    required this.icon,
    this.iconColor,
    this.subtitle,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(radiusMd),
        border: Border.all(color: AppColors.line),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 17, color: iconColor ?? AppColors.textDim),
                const SizedBox(width: 8),
                Text(
                  title.toUpperCase(),
                  style: const TextStyle(
                    fontFamily: AppFonts.display,
                    fontWeight: FontWeight.w700,
                    fontSize: 12,
                    letterSpacing: 0.4,
                    color: AppColors.text,
                  ),
                ),
              ],
            ),
            if (subtitle != null) ...[
              const SizedBox(height: 8),
              Text(
                subtitle!,
                style: const TextStyle(
                  color: AppColors.dim,
                  fontFamily: AppFonts.body,
                  fontSize: 11.5,
                  height: 1.5,
                ),
              ),
            ],
            const SizedBox(height: 14),
            ...children,
          ],
        ),
      ),
    );
  }
}

class _LocationSharingGroup extends StatelessWidget {
  final LocationService locationService;
  const _LocationSharingGroup({required this.locationService});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: locationService,
      builder: (context, _) {
        final sharing = locationService.isSharing;
        final error = locationService.lastError;
        final pos = locationService.lastPosition;
        final color = sharing && error == null
            ? AppColors.green
            : (sharing ? AppColors.amber : AppColors.red);
        final label = !sharing
            ? (error ?? 'Not sharing location')
            : error != null
            ? 'Sharing, but last update failed: $error'
            : pos != null
            ? 'Sharing — last fix ±${pos.accuracy.round()}m'
            : 'Sharing — waiting for first fix';

        return _SettingsGroup(
          title: 'Location Sharing',
          icon: Icons.my_location_rounded,
          iconColor: AppColors.cyan,
          subtitle:
              'While this app is open, your phone\'s GPS position is sent to '
              'the ground station so it can show the nearest rescuer to a '
              'detected survivor. Stops the moment the app is closed — '
              'this is not a background tracking service.',
          children: [
            Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: const TextStyle(fontFamily: AppFonts.body, fontSize: 12.5, color: AppColors.text),
                  ),
                ),
              ],
            ),
            if (!sharing) ...[
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed: () => locationService.start(),
                  child: const Text('Grant permission & share location'),
                ),
              ),
            ],
          ],
        );
      },
    );
  }
}

class _StatusCard extends StatelessWidget {
  final ConnectionManager connection;
  const _StatusCard({required this.connection});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: connection,
      builder: (context, _) {
        final mode = connection.mode;
        final color = switch (mode) {
          ConnectionMode.local => AppColors.green,
          ConnectionMode.cloud => AppColors.cyan,
          ConnectionMode.offline => AppColors.red,
        };
        return DecoratedBox(
          decoration: BoxDecoration(
            color: AppColors.surfaceSunken,
            borderRadius: BorderRadius.circular(radiusMd),
            border: Border.all(color: AppColors.line),
          ),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    mode.label,
                    style: const TextStyle(
                      fontFamily: AppFonts.body,
                      fontSize: 13,
                    ),
                  ),
                ),
                TextButton(
                  onPressed: connection.probeNow,
                  child: const Text('TEST NOW'),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
