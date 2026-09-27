import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

/// Frames a child (the live video) with targeting-style corner brackets --
/// the same visual language as the app icon (a lock/detection mark), so
/// the live feed feels like part of the same system rather than a bare
/// rectangle of video dropped onto the screen.
class ViewfinderFrame extends StatelessWidget {
  final Widget child;
  const ViewfinderFrame({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radiusMd),
        boxShadow: cardShadow(),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(radiusMd),
        child: AspectRatio(
          aspectRatio: 4 / 3,
          child: Stack(
            fit: StackFit.expand,
            children: [
              child,
              IgnorePointer(
                child: CustomPaint(painter: _CornerBracketsPainter()),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CornerBracketsPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.cyan.withValues(alpha: 0.85)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round;

    const len = 22.0;
    const inset = 12.0;

    // Top-left
    canvas.drawLine(const Offset(inset, inset + len), const Offset(inset, inset), paint);
    canvas.drawLine(const Offset(inset, inset), const Offset(inset + len, inset), paint);
    // Top-right
    canvas.drawLine(Offset(size.width - inset - len, inset), Offset(size.width - inset, inset), paint);
    canvas.drawLine(
        Offset(size.width - inset, inset), Offset(size.width - inset, inset + len), paint);
    // Bottom-left
    canvas.drawLine(
        Offset(inset, size.height - inset - len), Offset(inset, size.height - inset), paint);
    canvas.drawLine(
        Offset(inset, size.height - inset), Offset(inset + len, size.height - inset), paint);
    // Bottom-right
    canvas.drawLine(Offset(size.width - inset - len, size.height - inset),
        Offset(size.width - inset, size.height - inset), paint);
    canvas.drawLine(Offset(size.width - inset, size.height - inset - len),
        Offset(size.width - inset, size.height - inset), paint);
  }

  @override
  bool shouldRepaint(covariant _CornerBracketsPainter oldDelegate) => false;
}
