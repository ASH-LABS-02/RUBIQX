import 'package:flutter/material.dart';
import '../services/alerts_controller.dart';
import '../services/connection_manager.dart';
import '../theme/app_theme.dart';
import '../widgets/charts.dart';
import '../widgets/connection_banner.dart';

/// Mission insights -- summary numbers and the same detections-over-time /
/// source-breakdown visualisations the Command Center web dashboard shows,
/// so a rescue team member glancing at their phone gets the same picture a
/// coordinator sees on the ground-station screen.
class StatsScreen extends StatelessWidget {
  final AlertsController controller;
  final ConnectionManager connection;

  const StatsScreen({super.key, required this.controller, required this.connection});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        ConnectionBanner(connection: connection),
        Expanded(
          child: ListenableBuilder(
            listenable: controller,
            builder: (context, _) {
              final alerts = controller.alerts;
              final verified = alerts.where((a) => a.thermalVerified == true).length;
              final unconfirmed =
                  alerts.where((a) => !a.isFromMesh && a.thermalVerified == false).length;
              final mesh = alerts.where((a) => a.isFromMesh).length;

              return RefreshIndicator(
                color: AppColors.cyan,
                backgroundColor: AppColors.surface,
                onRefresh: controller.refresh,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: _StatTile(
                            label: 'Total detections',
                            value: '${alerts.length}',
                            color: AppColors.cyan,
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: _StatTile(
                            label: 'Thermal-verified',
                            value: '$verified',
                            color: AppColors.green,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _Card(
                      title: 'Detections timeline',
                      subtitle: 'last 20 minutes',
                      child: alerts.isEmpty
                          ? const _EmptyChartHint()
                          : Column(
                              children: [
                                DetectionsTimelineChart(alerts: alerts),
                                const SizedBox(height: 10),
                                Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    _LegendDot(color: AppColors.cyan, label: 'Camera'),
                                    const SizedBox(width: 16),
                                    _LegendDot(color: AppColors.amber, label: 'LoRa mesh'),
                                  ],
                                ),
                              ],
                            ),
                    ),
                    const SizedBox(height: 16),
                    _Card(
                      title: 'Detection sources',
                      subtitle: '${alerts.length} total',
                      child: alerts.isEmpty
                          ? const _EmptyChartHint()
                          : Row(
                              children: [
                                RingChart(
                                  segments: [
                                    RingSegment(verified.toDouble(), AppColors.green),
                                    RingSegment(unconfirmed.toDouble(), AppColors.amber),
                                    RingSegment(mesh.toDouble(), AppColors.cyan),
                                  ],
                                  center: Text(
                                    '${alerts.length}',
                                    style: const TextStyle(
                                      fontFamily: AppFonts.display,
                                      fontWeight: FontWeight.w700,
                                      fontSize: 22,
                                      color: AppColors.text,
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 20),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      _LegendRow(
                                        color: AppColors.green,
                                        label: 'Thermal-verified',
                                        count: verified,
                                      ),
                                      const SizedBox(height: 8),
                                      _LegendRow(
                                        color: AppColors.amber,
                                        label: 'Unconfirmed',
                                        count: unconfirmed,
                                      ),
                                      const SizedBox(height: 8),
                                      _LegendRow(
                                        color: AppColors.cyan,
                                        label: 'Via LoRa mesh',
                                        count: mesh,
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                    ),
                  ],
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _StatTile extends StatelessWidget {
  final String label;
  final String value;
  final Color color;
  const _StatTile({required this.label, required this.value, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(radiusMd),
        border: Border.all(color: AppColors.line),
        boxShadow: cardShadow(),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            value,
            style: TextStyle(
              fontFamily: AppFonts.display,
              fontWeight: FontWeight.w700,
              fontSize: 26,
              color: color,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: const TextStyle(
              fontFamily: AppFonts.body,
              fontSize: 11.5,
              color: AppColors.textDim,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _Card extends StatelessWidget {
  final String title;
  final String subtitle;
  final Widget child;
  const _Card({required this.title, required this.subtitle, required this.child});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(radiusMd),
        border: Border.all(color: AppColors.line),
        boxShadow: cardShadow(),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontFamily: AppFonts.body,
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                  color: AppColors.text,
                ),
              ),
              Text(
                subtitle,
                style: const TextStyle(fontFamily: AppFonts.data, fontSize: 10.5, color: AppColors.dim),
              ),
            ],
          ),
          const SizedBox(height: 14),
          child,
        ],
      ),
    );
  }
}

class _EmptyChartHint extends StatelessWidget {
  const _EmptyChartHint();
  @override
  Widget build(BuildContext context) {
    return const SizedBox(
      height: 80,
      child: Center(
        child: Text(
          'No detections yet',
          style: TextStyle(fontFamily: AppFonts.body, color: AppColors.dim, fontSize: 12.5),
        ),
      ),
    );
  }
}

class _LegendDot extends StatelessWidget {
  final Color color;
  final String label;
  const _LegendDot({required this.color, required this.label});
  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 6),
        Text(label,
            style: const TextStyle(fontFamily: AppFonts.body, fontSize: 11, color: AppColors.textDim)),
      ],
    );
  }
}

class _LegendRow extends StatelessWidget {
  final Color color;
  final String label;
  final int count;
  const _LegendRow({required this.color, required this.label, required this.count});
  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(width: 10, height: 10, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 8),
        Expanded(
          child: Text(label,
              style: const TextStyle(fontFamily: AppFonts.body, fontSize: 12.5, color: AppColors.text)),
        ),
        Text('$count',
            style: const TextStyle(
                fontFamily: AppFonts.data, fontSize: 13, fontWeight: FontWeight.w700, color: AppColors.text)),
      ],
    );
  }
}
