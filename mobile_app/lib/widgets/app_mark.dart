import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

/// A small vector rendition of the app icon's mark (radar rings + the same
/// red detection ping used throughout the UI) for use in-app -- the app
/// bar, the settings header -- where a crisp scalable draw reads better
/// than embedding the raster launcher icon at a tiny size.
class AppMark extends StatelessWidget {
  final double size;
  const AppMark({super.key, this.size = 26});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _AppMarkPainter()),
    );
  }
}

class _AppMarkPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final c = size.width / 2;
    final r = size.width * 0.46;

    final ringPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = size.width * 0.09
      ..color = AppColors.cyan;

    canvas.drawCircle(Offset(c, c), r, ringPaint..color = AppColors.cyan.withValues(alpha: 0.7));
    canvas.drawCircle(Offset(c, c), r * 0.56, ringPaint..color = AppColors.cyan);
    canvas.drawCircle(Offset(c, c), r * 0.20, Paint()..color = AppColors.cyan);

    final angle = -40 * math.pi / 180;
    final pingCx = c + r * math.cos(angle);
    final pingCy = c + r * math.sin(angle);
    final pingR = r * 0.30;

    canvas.drawCircle(Offset(pingCx, pingCy), pingR * 1.25, Paint()..color = AppColors.bg);
    canvas.drawCircle(Offset(pingCx, pingCy), pingR, Paint()..color = AppColors.red);
  }

  @override
  bool shouldRepaint(covariant _AppMarkPainter oldDelegate) => false;
}
