import 'package:flutter/material.dart';
import '../models/connection_mode.dart';
import '../services/connection_manager.dart';
import '../theme/app_theme.dart';

/// Persistent status strip so a rescuer always knows, at a glance, whether
/// alerts are live right now or the app is showing stale cached data --
/// the single most important thing to be honest about in a field tool.
class ConnectionBanner extends StatelessWidget {
  final ConnectionManager connection;
  const ConnectionBanner({super.key, required this.connection});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: connection,
      builder: (context, _) {
        final mode = connection.mode;
        final (color, icon) = switch (mode) {
          ConnectionMode.local => (AppColors.green, Icons.wifi_tethering_rounded),
          ConnectionMode.cloud => (AppColors.cyan, Icons.cloud_done_rounded),
          ConnectionMode.offline => (AppColors.red, Icons.cloud_off_rounded),
        };
        final live = mode != ConnectionMode.offline;

        return Container(
          decoration: BoxDecoration(
            color: AppColors.surfaceSunken,
            border: const Border(bottom: BorderSide(color: AppColors.lineFaint)),
          ),
          child: Material(
            color: Colors.transparent,
            child: InkWell(
              onTap: connection.probeNow,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: Row(
                  children: [
                    _PulseDot(color: color, animate: live),
                    const SizedBox(width: 10),
                    Icon(icon, size: 14, color: color),
                    const SizedBox(width: 6),
                    Text(
                      mode.label.toUpperCase(),
                      style: TextStyle(
                        color: color,
                        fontFamily: AppFonts.body,
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.6,
                      ),
                    ),
                    const Spacer(),
                    Text(
                      mode == ConnectionMode.offline
                          ? _lastSeenText(connection.lastSuccessAt)
                          : 'tap to refresh',
                      style: const TextStyle(
                        color: AppColors.dim,
                        fontFamily: AppFonts.data,
                        fontSize: 10.5,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  String _lastSeenText(DateTime? last) {
    if (last == null) return 'never connected';
    final ago = DateTime.now().difference(last);
    if (ago.inSeconds < 60) return 'last seen ${ago.inSeconds}s ago';
    if (ago.inMinutes < 60) return 'last seen ${ago.inMinutes}m ago';
    return 'last seen ${ago.inHours}h ago';
  }
}

/// A softly breathing dot rather than a static one -- the small cue that
/// tells a rescuer, without reading anything, that this screen is alive
/// and not frozen.
class _PulseDot extends StatefulWidget {
  final Color color;
  final bool animate;
  const _PulseDot({required this.color, required this.animate});

  @override
  State<_PulseDot> createState() => _PulseDotState();
}

class _PulseDotState extends State<_PulseDot> with SingleTickerProviderStateMixin {
  // Built eagerly in initState, not as a `late final` field initializer --
  // the latter only runs on first *read*, and if that first read happened
  // to be from dispose() (e.g. a widget torn down before ever painting a
  // frame, which is exactly what a fast test pump does), constructing an
  // AnimationController there tries to look up this element's TickerMode
  // ancestor after the element is already deactivated. Crashes with
  // "Looking up a deactivated widget's ancestor is unsafe" -- caught by
  // the widget test, not by `flutter analyze`, which is why it's worth
  // actually running the test suite rather than trusting analyze alone.
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.animate) {
      return Container(
        width: 8,
        height: 8,
        decoration: BoxDecoration(color: widget.color, shape: BoxShape.circle),
      );
    }
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final t = _controller.value;
        return Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            color: widget.color,
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(
                color: widget.color.withValues(alpha: 0.55 * (1 - t)),
                blurRadius: 6 + 6 * t,
                spreadRadius: 1 + 3 * t,
              ),
            ],
          ),
        );
      },
    );
  }
}
