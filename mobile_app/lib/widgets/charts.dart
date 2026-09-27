import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/alert.dart';
import '../theme/app_theme.dart';

/// Detections-over-time bar chart -- deliberately hand-rolled with
/// CustomPainter rather than a charting package: this app's whole premise
/// is working with no internet and a minimal footprint, and a chart
/// library is a lot of weight for two bars per bucket. Mirrors the same
/// binned-bar approach the Command Center web dashboard uses, so the two
/// UIs read as one system.
class DetectionsTimelineChart extends StatelessWidget {
  final List<Alert> alerts;
  final int windowMinutes;
  final int buckets;

  const DetectionsTimelineChart({
    super.key,
    required this.alerts,
    this.windowMinutes = 20,
    this.buckets = 20,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 150,
      child: CustomPaint(
        painter: _TimelinePainter(
          alerts: alerts,
          windowMinutes: windowMinutes,
          buckets: buckets,
        ),
        size: Size.infinite,
      ),
    );
  }
}

class _TimelinePainter extends CustomPainter {
  final List<Alert> alerts;
  final int windowMinutes;
  final int buckets;

  _TimelinePainter({required this.alerts, required this.windowMinutes, required this.buckets});

  @override
  void paint(Canvas canvas, Size size) {
    final nowS = DateTime.now().millisecondsSinceEpoch / 1000;
    final bucketSpanS = (windowMinutes * 60) / buckets;
    final cameraCounts = List<int>.filled(buckets, 0);
    final meshCounts = List<int>.filled(buckets, 0);

    for (final a in alerts) {
      final b = ((nowS - a.createdAt) / bucketSpanS).floor();
      if (b < 0 || b >= buckets) continue;
      final idx = buckets - 1 - b;
      if (a.isFromMesh) {
        meshCounts[idx]++;
      } else {
        cameraCounts[idx]++;
      }
    }

    final maxCount = [
      1,
      ...cameraCounts,
      ...meshCounts,
    ].reduce(math.max);

    final gridPaint = Paint()
      ..color = AppColors.lineFaint
      ..strokeWidth = 1;
    for (var i = 0; i <= 2; i++) {
      final y = size.height * i / 2;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    final colW = size.width / buckets;
    final plotH = size.height - 18;
    final cameraPaint = Paint()..color = AppColors.cyan;
    final meshPaint = Paint()..color = AppColors.amber;

    for (var i = 0; i < buckets; i++) {
      final x = i * colW;
      if (cameraCounts[i] > 0) {
        final h = (cameraCounts[i] / maxCount) * plotH;
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(x + colW * 0.15, plotH - h, colW * 0.32, h),
            const Radius.circular(2),
          ),
          cameraPaint,
        );
      }
      if (meshCounts[i] > 0) {
        final h = (meshCounts[i] / maxCount) * plotH;
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromLTWH(x + colW * 0.53, plotH - h, colW * 0.32, h),
            const Radius.circular(2),
          ),
          meshPaint,
        );
      }
    }

    final tp = (String text) => TextPainter(
          text: TextSpan(
            text: text,
            style: const TextStyle(fontFamily: AppFonts.data, fontSize: 10, color: AppColors.dim),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
    tp('${windowMinutes}m ago').paint(canvas, Offset(0, size.height - 14));
    final nowLabel = tp('now');
    nowLabel.paint(canvas, Offset(size.width - nowLabel.width, size.height - 14));
  }

  @override
  bool shouldRepaint(covariant _TimelinePainter oldDelegate) =>
      oldDelegate.alerts.length != alerts.length;
}

/// Ring/donut breakdown -- e.g. verified vs unconfirmed vs mesh. A single
/// small reusable painter driven by a list of (value, color) segments so
/// it can be reused for more than one breakdown without copy-pasting.
class RingChart extends StatelessWidget {
  final List<RingSegment> segments;
  final double size;
  final double strokeWidth;
  final Widget? center;

  const RingChart({
    super.key,
    required this.segments,
    this.size = 120,
    this.strokeWidth = 16,
    this.center,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: Size(size, size),
            painter: _RingPainter(segments: segments, strokeWidth: strokeWidth),
          ),
          if (center != null) center!,
        ],
      ),
    );
  }
}

class RingSegment {
  final double value;
  final Color color;
  const RingSegment(this.value, this.color);
}

class _RingPainter extends CustomPainter {
  final List<RingSegment> segments;
  final double strokeWidth;
  _RingPainter({required this.segments, required this.strokeWidth});

  @override
  void paint(Canvas canvas, Size size) {
    final total = segments.fold<double>(0, (s, e) => s + e.value);
    final rect = Rect.fromLTWH(
      strokeWidth / 2,
      strokeWidth / 2,
      size.width - strokeWidth,
      size.height - strokeWidth,
    );

    final track = Paint()
      ..color = AppColors.lineFaint
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;
    canvas.drawArc(rect, 0, 2 * math.pi, false, track);

    if (total <= 0) return;

    var start = -math.pi / 2;
    for (final seg in segments) {
      if (seg.value <= 0) continue;
      final sweep = (seg.value / total) * 2 * math.pi;
      final paint = Paint()
        ..color = seg.color
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.butt;
      canvas.drawArc(rect, start, sweep, false, paint);
      start += sweep;
    }
  }

  @override
  bool shouldRepaint(covariant _RingPainter oldDelegate) => true;
}
