import 'dart:async';
import 'package:flutter/material.dart';
import '../models/connection_mode.dart';
import '../services/alerts_controller.dart';
import '../services/connection_manager.dart';
import '../theme/app_theme.dart';
import 'app_mark.dart';

/// The app-wide "device shell" -- a persistent status header and a
/// segmented bottom panel -- deliberately built instead of a stock
/// Material AppBar/NavigationBar. A rescuer's mental model for this thing
/// should be "field terminal", not "phone app": live telemetry (clock,
/// signal) always on screen, sharp panel edges, numbered segments --
/// closer to how a handheld radio or tactical tablet presents itself than
/// to a consumer app's rounded chrome.

/// Small vertical signal-strength bars, filled 0-4 by connection quality.
class SignalBars extends StatelessWidget {
  final int level;
  final Color color;
  const SignalBars({super.key, required this.level, required this.color});

  static const _heights = [5.0, 8.0, 11.0, 14.0];

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: List.generate(4, (i) {
        final filled = i < level;
        return Container(
          width: 3,
          height: _heights[i],
          margin: const EdgeInsets.only(right: 2),
          decoration: BoxDecoration(
            color: filled ? color : AppColors.line,
            borderRadius: BorderRadius.circular(1),
          ),
        );
      }),
    );
  }
}

/// A ticking HH:MM:SS readout -- the single cheapest cue that this screen
/// is a live instrument rather than a static page.
class LiveClock extends StatefulWidget {
  const LiveClock({super.key});

  @override
  State<LiveClock> createState() => _LiveClockState();
}

class _LiveClockState extends State<LiveClock> {
  late final Timer _timer;
  DateTime _now = DateTime.now();

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _now = DateTime.now());
    });
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  static String _two(int n) => n.toString().padLeft(2, '0');

  @override
  Widget build(BuildContext context) {
    return Text(
      '${_two(_now.hour)}:${_two(_now.minute)}:${_two(_now.second)}',
      style: const TextStyle(
        fontFamily: AppFonts.data,
        fontSize: 12,
        fontWeight: FontWeight.w600,
        color: AppColors.textDim,
        letterSpacing: 0.4,
      ),
    );
  }
}

/// Persistent top status strip: unit identity plate, live clock and
/// signal readout, new-alert flag. Replaces the standard AppBar app-wide.
class DeviceHeader extends StatelessWidget {
  final ConnectionManager connection;
  final AlertsController controller;
  const DeviceHeader({
    super.key,
    required this.connection,
    required this.controller,
  });

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([connection, controller]),
      builder: (context, _) {
        final mode = connection.mode;
        final (sigLevel, sigColor) = switch (mode) {
          ConnectionMode.local => (4, AppColors.green),
          ConnectionMode.cloud => (2, AppColors.cyan),
          ConnectionMode.offline => (0, AppColors.red),
        };
        final n = controller.newCount;

        return Container(
          decoration: BoxDecoration(
            color: AppColors.surface,
            border: const Border(
              bottom: BorderSide(color: AppColors.cyan, width: 2),
            ),
            boxShadow: cardShadow(opacity: 0.05),
          ),
          child: SafeArea(
            bottom: false,
            child: SizedBox(
              height: 58,
              child: Row(
                children: [
                  const SizedBox(width: 14),
                  Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      border: Border.all(color: AppColors.line),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: const Center(child: AppMark(size: 20)),
                  ),
                  const SizedBox(width: 10),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text.rich(
                        TextSpan(
                          children: [
                            TextSpan(
                              text: 'SAR',
                              style: TextStyle(
                                fontFamily: AppFonts.display,
                                fontWeight: FontWeight.w700,
                                fontSize: 15,
                                color: AppColors.text,
                              ),
                            ),
                            TextSpan(
                              text: ' RESCUE',
                              style: TextStyle(
                                fontFamily: AppFonts.display,
                                fontWeight: FontWeight.w700,
                                fontSize: 15,
                                color: AppColors.cyan,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const Text(
                        'FIELD TERMINAL',
                        style: TextStyle(
                          fontFamily: AppFonts.data,
                          fontSize: 8.5,
                          letterSpacing: 1.1,
                          fontWeight: FontWeight.w600,
                          color: AppColors.dim,
                        ),
                      ),
                    ],
                  ),
                  const Spacer(),
                  if (n > 0) ...[
                    AnimatedContainer(
                      duration: const Duration(milliseconds: 200),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 7,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: AppColors.red,
                        borderRadius: BorderRadius.circular(3),
                        boxShadow: glowShadow(AppColors.red, opacity: 0.35),
                      ),
                      child: Text(
                        '$n NEW',
                        style: const TextStyle(
                          fontFamily: AppFonts.data,
                          fontSize: 9.5,
                          fontWeight: FontWeight.w800,
                          color: Colors.white,
                          letterSpacing: 0.3,
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                  ],
                  SignalBars(level: sigLevel, color: sigColor),
                  const SizedBox(width: 10),
                  const LiveClock(),
                  const SizedBox(width: 14),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class DeviceNavItem {
  final IconData icon;
  final IconData selectedIcon;
  final String label;
  const DeviceNavItem({
    required this.icon,
    required this.selectedIcon,
    required this.label,
  });
}

/// A segmented, numbered control panel rather than a floating Material
/// pill-indicator nav bar -- sharp dividers between segments and a top LED
/// strip on the active one, styled closer to a device's physical button
/// row than to a consumer app's bottom nav.
class DeviceNavBar extends StatelessWidget {
  final int selectedIndex;
  final ValueChanged<int> onSelect;
  final List<DeviceNavItem> items;
  const DeviceNavBar({
    super.key,
    required this.selectedIndex,
    required this.onSelect,
    required this.items,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: const Border(top: BorderSide(color: AppColors.cyan, width: 2)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.06),
            blurRadius: 16,
            offset: const Offset(0, -4),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 66,
          child: Row(
            children: List.generate(items.length, (i) {
              final selected = i == selectedIndex;
              final item = items[i];
              return Expanded(
                child: Material(
                  color: Colors.transparent,
                  child: InkWell(
                    onTap: () => onSelect(i),
                    child: Container(
                      decoration: BoxDecoration(
                        color: selected
                            ? AppColors.cyan.withValues(alpha: 0.07)
                            : Colors.transparent,
                        border: Border(
                          right: BorderSide(
                            color: AppColors.lineFaint,
                            width: i == items.length - 1 ? 0 : 1,
                          ),
                        ),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          AnimatedContainer(
                            duration: const Duration(milliseconds: 200),
                            height: 3,
                            width: selected ? 26 : 0,
                            margin: const EdgeInsets.only(bottom: 6),
                            decoration: BoxDecoration(
                              color: AppColors.cyan,
                              borderRadius: BorderRadius.circular(2),
                            ),
                          ),
                          Icon(
                            selected ? item.selectedIcon : item.icon,
                            size: 21,
                            color: selected ? AppColors.cyan : AppColors.dim,
                          ),
                          const SizedBox(height: 3),
                          Text(
                            item.label.toUpperCase(),
                            style: TextStyle(
                              fontFamily: AppFonts.data,
                              fontSize: 9,
                              fontWeight: FontWeight.w700,
                              letterSpacing: 0.7,
                              color: selected ? AppColors.cyan : AppColors.dim,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              );
            }),
          ),
        ),
      ),
    );
  }
}
