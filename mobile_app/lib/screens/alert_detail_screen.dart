import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../models/alert.dart';
import '../services/alert_service.dart';
import '../services/alerts_controller.dart';
import '../theme/app_theme.dart';

class AlertDetailScreen extends StatefulWidget {
  final Alert alert;
  final AlertsController controller;
  final AlertService alertService;

  const AlertDetailScreen({
    super.key,
    required this.alert,
    required this.controller,
    required this.alertService,
  });

  @override
  State<AlertDetailScreen> createState() => _AlertDetailScreenState();
}

class _AlertDetailScreenState extends State<AlertDetailScreen> {
  bool _busy = false;

  Alert get _live => widget.controller.alerts
      .firstWhere((a) => a.id == widget.alert.id, orElse: () => widget.alert);

  Future<void> _setStatus(String status) async {
    setState(() => _busy = true);
    try {
      await widget.controller.updateStatus(
        widget.alert.id,
        status,
        widget.controller.connection.settings.rescuerName.isEmpty
            ? null
            : widget.controller.connection.settings.rescuerName,
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Marked as ${status.replaceAll('_', ' ')}')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _navigate(Alert alert) async {
    final uri = Uri.parse('geo:${alert.lat},${alert.lon}?q=${alert.lat},${alert.lon}');
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
      final webUri =
          Uri.parse('https://www.google.com/maps/search/?api=1&query=${alert.lat},${alert.lon}');
      await launchUrl(webUri, mode: LaunchMode.externalApplication);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final alert = _live;
        final imageUrl = widget.alertService.resolveMediaUrl(alert.imageUrl);
        final statusColor = AppColors.forStatus(alert.status);

        return Scaffold(
          extendBodyBehindAppBar: true,
          appBar: AppBar(
            backgroundColor: Colors.transparent,
            title: const Text('Detection'),
          ),
          body: Column(
            children: [
              Stack(
                children: [
                  Hero(
                    tag: 'alert-image-${alert.id}',
                    child: AspectRatio(
                      aspectRatio: 4 / 3,
                      child: imageUrl == null
                          ? const ColoredBox(
                              color: AppColors.surfaceSunken,
                              child: Icon(Icons.image_not_supported_outlined,
                                  color: AppColors.dim, size: 40),
                            )
                          : Image.network(
                              imageUrl,
                              fit: BoxFit.cover,
                              errorBuilder: (_, __, ___) => const ColoredBox(
                                color: AppColors.surfaceSunken,
                                child: Icon(Icons.broken_image_outlined,
                                    color: AppColors.dim, size: 40),
                              ),
                            ),
                    ),
                  ),
                  // Scrim so the AppBar (which sits over the image) stays
                  // legible regardless of what's in the photo.
                  Positioned(
                    left: 0,
                    right: 0,
                    top: 0,
                    height: 110,
                    child: IgnorePointer(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          gradient: LinearGradient(
                            begin: Alignment.topCenter,
                            end: Alignment.bottomCenter,
                            colors: [Colors.black.withValues(alpha: 0.55), Colors.transparent],
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(18, 18, 18, 100),
                  children: [
                    Row(
                      children: [
                        Container(
                          width: 10,
                          height: 10,
                          decoration: BoxDecoration(color: statusColor, shape: BoxShape.circle),
                        ),
                        const SizedBox(width: 8),
                        Text(
                          'HUMAN DETECTED',
                          style: TextStyle(
                            fontFamily: AppFonts.display,
                            fontWeight: FontWeight.w700,
                            fontSize: 20,
                            letterSpacing: -0.2,
                            color: statusColor,
                          ),
                        ),
                        const Spacer(),
                        _StatusPill(status: alert.status),
                      ],
                    ),
                    const SizedBox(height: 20),
                    DecoratedBox(
                      decoration: BoxDecoration(
                        color: AppColors.surface,
                        borderRadius: BorderRadius.circular(radiusMd),
                        border: Border.all(color: AppColors.line),
                      ),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
                        child: Column(
                          children: [
                            _DetailRow(Icons.groups_rounded, 'Persons detected', '${alert.personCount}'),
                            _DetailRow(Icons.speed_rounded, 'Confidence',
                                '${alert.confidence.toStringAsFixed(1)}%'),
                            _DetailRow(Icons.schedule_rounded, 'Detected at',
                                alert.createdAtLocal.toString().substring(0, 19)),
                            _DetailRow(
                              alert.isFromMesh ? Icons.router_rounded : Icons.videocam_rounded,
                              'Source',
                              alert.isFromMesh ? 'LoRa mesh relay' : 'Ground camera (direct)',
                            ),
                            if (alert.isFromMesh) ...[
                              _DetailRow(Icons.alt_route_rounded, 'Route',
                                  '${alert.route ?? '?'}, ${alert.hopsOrZero} hop(s)'),
                              if (alert.rssiDbm != null)
                                _DetailRow(Icons.podcasts_rounded, 'Signal',
                                    '${alert.rssiDbm!.toStringAsFixed(0)} dBm'),
                            ],
                            _DetailRow(
                              Icons.place_rounded,
                              'Location',
                              alert.hasLocation
                                  ? '${alert.lat!.toStringAsFixed(6)}, ${alert.lon!.toStringAsFixed(6)}'
                                  : 'No GPS lock at detection time',
                              last: alert.acknowledgedBy == null,
                            ),
                            if (alert.acknowledgedBy != null)
                              _DetailRow(Icons.person_pin_rounded, 'Handled by',
                                  alert.acknowledgedBy!, last: true),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),
                    if (alert.hasLocation)
                      SizedBox(
                        width: double.infinity,
                        child: OutlinedButton.icon(
                          onPressed: () => _navigate(alert),
                          icon: const Icon(Icons.navigation_rounded, size: 18),
                          label: const Text('Navigate here'),
                        ),
                      )
                    else
                      // Filling this with an explanation rather than leaving
                      // the "no GPS" case as blank space below the detail
                      // card -- the reason matters to a rescuer deciding
                      // how to act on this alert, and until Drone-model's
                      // detection loop has GPS wired in, this is what most
                      // alerts will show.
                      const _NoLocationNotice(),
                  ],
                ),
              ),
            ],
          ),
          bottomNavigationBar: _ActionBar(
            alert: alert,
            busy: _busy,
            onDispatch: () => _setStatus('acknowledged'),
            onResolve: () => _setStatus('resolved'),
            onFalseAlarm: () => _setStatus('false_alarm'),
          ),
        );
      },
    );
  }
}

class _ActionBar extends StatelessWidget {
  final Alert alert;
  final bool busy;
  final VoidCallback onDispatch;
  final VoidCallback onResolve;
  final VoidCallback onFalseAlarm;

  const _ActionBar({
    required this.alert,
    required this.busy,
    required this.onDispatch,
    required this.onResolve,
    required this.onFalseAlarm,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.fromLTRB(16, 10, 16, 10 + MediaQuery.of(context).padding.bottom),
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.line)),
      ),
      child: Row(
        children: [
          Expanded(
            flex: 3,
            child: FilledButton.icon(
              onPressed: busy || alert.status != 'new' ? null : onDispatch,
              icon: const Icon(Icons.directions_run_rounded, size: 18),
              label: const Text('Dispatch team'),
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.amber,
                foregroundColor: Colors.black,
              ),
            ),
          ),
          const SizedBox(width: 8),
          _IconOnlyButton(
            icon: Icons.check_circle_rounded,
            color: AppColors.green,
            tooltip: 'Mark resolved',
            onPressed: busy || alert.status == 'resolved' ? null : onResolve,
          ),
          const SizedBox(width: 8),
          _IconOnlyButton(
            icon: Icons.block_rounded,
            color: AppColors.dim,
            tooltip: 'False alarm',
            onPressed: busy || alert.status == 'false_alarm' ? null : onFalseAlarm,
          ),
        ],
      ),
    );
  }
}

class _IconOnlyButton extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String tooltip;
  final VoidCallback? onPressed;

  const _IconOnlyButton({
    required this.icon,
    required this.color,
    required this.tooltip,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: AppColors.surfaceRaised,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusSm),
          side: const BorderSide(color: AppColors.line),
        ),
        child: InkWell(
          borderRadius: BorderRadius.circular(radiusSm),
          onTap: onPressed,
          child: Padding(
            padding: const EdgeInsets.all(13),
            child: Icon(icon, size: 20, color: onPressed == null ? AppColors.dim : color),
          ),
        ),
      ),
    );
  }
}

class _NoLocationNotice extends StatelessWidget {
  const _NoLocationNotice();

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: AppColors.amber.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(radiusMd),
        border: Border.all(color: AppColors.amber.withValues(alpha: 0.25)),
      ),
      child: const Padding(
        padding: EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.gps_off_rounded, size: 18, color: AppColors.amber),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'No GPS position was available when this was detected. '
                'The photo is the only lead on location — check it for '
                'landmarks, or cross-reference the camera\'s last known '
                'position on the Map tab.',
                style: TextStyle(
                    color: AppColors.textDim, fontFamily: AppFonts.body, fontSize: 12, height: 1.5),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  final bool last;
  const _DetailRow(this.icon, this.label, this.value, {this.last = false});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      decoration: BoxDecoration(
        border: last ? null : const Border(bottom: BorderSide(color: AppColors.lineFaint)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 16, color: AppColors.dim),
          const SizedBox(width: 10),
          SizedBox(
            width: 108,
            child: Text(label,
                style: const TextStyle(color: AppColors.textDim, fontFamily: AppFonts.body, fontSize: 12)),
          ),
          Expanded(
            child: Text(value,
                style: const TextStyle(fontFamily: AppFonts.data, fontSize: 12.5, height: 1.3)),
          ),
        ],
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  final String status;
  const _StatusPill({required this.status});

  @override
  Widget build(BuildContext context) {
    final color = AppColors.forStatus(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(
        status.replaceAll('_', ' ').toUpperCase(),
        style: TextStyle(
            color: color, fontFamily: AppFonts.body, fontSize: 10, fontWeight: FontWeight.w700),
      ),
    );
  }
}
