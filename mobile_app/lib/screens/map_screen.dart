import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/alert.dart';
import '../services/alerts_controller.dart';
import '../services/connection_manager.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_banner.dart';
import 'alert_detail_screen.dart';
import '../services/alert_service.dart';

/// A schematic, offline-safe map of detection locations.
///
/// Deliberately not a real basemap (Google Maps / OSM tiles): tile imagery
/// needs the internet to fetch (or a pre-downloaded offline cache this
/// prototype doesn't build), which contradicts the whole point of a tool
/// meant to keep working with zero connectivity. This plots alerts
/// relative to each other by lat/lon instead -- same approach the GCS web
/// dashboard uses. Good enough to see spread and relative direction; not a
/// substitute for a real basemap if/when offline tile caching is added.
class MapScreen extends StatelessWidget {
  final AlertsController controller;
  final AlertService alertService;
  final ConnectionManager connection;

  const MapScreen({
    super.key,
    required this.controller,
    required this.alertService,
    required this.connection,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        ConnectionBanner(connection: connection),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
          decoration: const BoxDecoration(
            color: AppColors.surfaceSunken,
            border: Border(bottom: BorderSide(color: AppColors.lineFaint)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.satellite_alt_outlined, size: 12, color: AppColors.dim),
              const SizedBox(width: 6),
              Text(
                'SCHEMATIC — RELATIVE POSITIONS, NO TILE SERVER NEEDED',
                style: TextStyle(
                  color: AppColors.dim,
                  fontFamily: AppFonts.data,
                  fontSize: 9.5,
                  letterSpacing: 0.3,
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: ListenableBuilder(
            listenable: controller,
            builder: (context, _) {
              final located = controller.alerts.where((a) => a.hasLocation).toList();
              if (located.isEmpty) {
                return const Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.map_outlined, color: AppColors.dim, size: 36),
                      SizedBox(height: 10),
                      Text(
                        'No detections with GPS location yet',
                        style: TextStyle(color: AppColors.dim, fontFamily: AppFonts.body, fontSize: 13),
                      ),
                    ],
                  ),
                );
              }
              return LayoutBuilder(
                builder: (context, constraints) => GestureDetector(
                  onTapUp: (details) => _handleTap(context, details, located, constraints),
                  child: CustomPaint(
                    size: Size(constraints.maxWidth, constraints.maxHeight),
                    painter: _SchematicMapPainter(alerts: located),
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }

  void _handleTap(
    BuildContext context,
    TapUpDetails details,
    List<Alert> located,
    BoxConstraints constraints,
  ) {
    final projection = _Projection(located, constraints.maxWidth, constraints.maxHeight);
    for (final alert in located) {
      final p = projection.project(alert.lat!, alert.lon!);
      if ((p - details.localPosition).distance < 22) {
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => AlertDetailScreen(
              alert: alert,
              controller: controller,
              alertService: alertService,
            ),
          ),
        );
        return;
      }
    }
  }
}

class _Projection {
  late final double lat0, lon0, mPerLon, scale;
  final double width, height;
  static const _pad = 40.0;

  _Projection(List<Alert> alerts, this.width, this.height) {
    lat0 = alerts.map((a) => a.lat!).reduce((a, b) => a + b) / alerts.length;
    lon0 = alerts.map((a) => a.lon!).reduce((a, b) => a + b) / alerts.length;
    mPerLon = 111320 * math.cos(lat0 * math.pi / 180);

    var maxSpanM = 50.0; // never zoom in past ~50m so a single point isn't a blank void
    for (final a in alerts) {
      final dx = (a.lon! - lon0) * mPerLon;
      final dy = (a.lat! - lat0) * 111320;
      maxSpanM = math.max(maxSpanM, math.max(dx.abs(), dy.abs()) * 2.2);
    }
    scale = (math.min(width, height) - _pad * 2) / maxSpanM;
  }

  Offset project(double lat, double lon) {
    final dx = (lon - lon0) * mPerLon * scale;
    final dy = (lat - lat0) * 111320 * scale;
    return Offset(width / 2 + dx, height / 2 - dy); // north up
  }
}

class _SchematicMapPainter extends CustomPainter {
  final List<Alert> alerts;
  _SchematicMapPainter({required this.alerts});

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = AppColors.surfaceSunken);

    final gridPaint = Paint()
      ..color = AppColors.lineFaint
      ..strokeWidth = 1;
    for (double x = 0; x < size.width; x += 40) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), gridPaint);
    }
    for (double y = 0; y < size.height; y += 40) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    final projection = _Projection(alerts, size.width, size.height);

    for (final alert in alerts) {
      final p = projection.project(alert.lat!, alert.lon!);
      final color = AppColors.forStatus(alert.status);

      // Soft glow, then a white-edged pin -- matches the app icon's own
      // "detection ping" treatment so the map reads as part of one system.
      canvas.drawCircle(p, 20, Paint()..color = color.withValues(alpha: 0.14));
      canvas.drawCircle(p, 8, Paint()..color = AppColors.bg);
      canvas.drawCircle(p, 6.5, Paint()..color = color);
      canvas.drawCircle(
        p,
        6.5,
        Paint()
          ..color = AppColors.bg
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.6,
      );

      final tp = TextPainter(
        text: TextSpan(
          text: '#${alert.id.substring(0, 4)}',
          style: TextStyle(color: color, fontSize: 10, fontFamily: AppFonts.data, fontWeight: FontWeight.w600),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, p + const Offset(12, -7));
    }

    // Scale bar
    final metres = _niceScale(size.width / projection.scale * 0.3);
    final barWidth = metres * projection.scale;
    final barY = size.height - 22.0;
    canvas.drawLine(
      Offset(16, barY),
      Offset(16 + barWidth, barY),
      Paint()
        ..color = AppColors.dim
        ..strokeWidth = 2,
    );
    final label = TextPainter(
      text: TextSpan(
        text: metres >= 1000 ? '${(metres / 1000).toStringAsFixed(1)} km' : '${metres.round()} m',
        style: const TextStyle(color: AppColors.dim, fontSize: 10),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    label.paint(canvas, Offset(16, barY - 16));
  }

  double _niceScale(double raw) {
    const steps = <double>[10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000];
    for (final s in steps) {
      if (s >= raw) return s;
    }
    return steps.last;
  }

  @override
  bool shouldRepaint(covariant _SchematicMapPainter old) => old.alerts != alerts;
}
