import 'package:flutter/material.dart';
import '../models/alert.dart';
import '../services/alert_service.dart';
import '../services/alerts_controller.dart';
import '../services/connection_manager.dart';
import '../theme/app_theme.dart';
import '../widgets/alert_card.dart';
import '../widgets/app_mark.dart';
import '../widgets/connection_banner.dart';
import 'alert_detail_screen.dart';

class AlertsScreen extends StatelessWidget {
  final AlertsController controller;
  final AlertService alertService;
  final ConnectionManager connection;

  const AlertsScreen({
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
        Expanded(
          child: ListenableBuilder(
            listenable: controller,
            builder: (context, _) {
              final alerts = controller.alerts;

              if (alerts.isEmpty && controller.loading) {
                return const Center(
                  child: CircularProgressIndicator(color: AppColors.cyan, strokeWidth: 2.4),
                );
              }

              if (alerts.isEmpty) {
                return RefreshIndicator(
                  color: AppColors.cyan,
                  backgroundColor: AppColors.surface,
                  onRefresh: controller.refresh,
                  child: ListView(
                    children: [
                      SizedBox(
                        height: MediaQuery.of(context).size.height * 0.62,
                        child: Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Opacity(opacity: 0.5, child: const AppMark(size: 52)),
                              const SizedBox(height: 18),
                              const Text(
                                'ALL CLEAR',
                                style: TextStyle(
                                  color: AppColors.textDim,
                                  fontFamily: AppFonts.display,
                                  fontWeight: FontWeight.w700,
                                  fontSize: 14,
                                  letterSpacing: 0.6,
                                ),
                              ),
                              const SizedBox(height: 4),
                              const Text(
                                'No detections yet',
                                style: TextStyle(
                                    color: AppColors.dim, fontFamily: AppFonts.body, fontSize: 12.5),
                              ),
                              if (controller.lastError != null) ...[
                                const SizedBox(height: 10),
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 32),
                                  child: Text(
                                    controller.lastError!,
                                    textAlign: TextAlign.center,
                                    style: const TextStyle(
                                        color: AppColors.red, fontFamily: AppFonts.data, fontSize: 10.5),
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              }

              return RefreshIndicator(
                color: AppColors.cyan,
                backgroundColor: AppColors.surface,
                onRefresh: controller.refresh,
                child: ListView.builder(
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  itemCount: alerts.length,
                  itemBuilder: (context, i) {
                    final alert = alerts[i];
                    return _EntranceFade(
                      key: ValueKey(alert.id),
                      child: AlertCard(
                        alert: alert,
                        imageUrl: alertService.resolveMediaUrl(alert.imageUrl),
                        onTap: () => _openDetail(context, alert),
                      ),
                    );
                  },
                ),
              );
            },
          ),
        ),
      ],
    );
  }

  void _openDetail(BuildContext context, Alert alert) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => AlertDetailScreen(
          alert: alert,
          controller: controller,
          alertService: alertService,
        ),
      ),
    );
  }
}

/// A brief fade+rise the first time a card is built. Cheap way to make new
/// alerts feel like they *arrived* rather than just appearing, without the
/// bookkeeping overhead of a full AnimatedList for a list that reorders on
/// every live push and status update.
class _EntranceFade extends StatefulWidget {
  final Widget child;
  const _EntranceFade({super.key, required this.child});

  @override
  State<_EntranceFade> createState() => _EntranceFadeState();
}

class _EntranceFadeState extends State<_EntranceFade> with SingleTickerProviderStateMixin {
  // Eager in initState, not a `late final` initializer -- see the identical
  // fix (and the reason) in connection_banner.dart's _PulseDotState.
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 320),
    )..forward();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final curved = CurvedAnimation(parent: _controller, curve: Curves.easeOutCubic);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
        position: Tween(begin: const Offset(0, 0.04), end: Offset.zero).animate(curved),
        child: widget.child,
      ),
    );
  }
}
