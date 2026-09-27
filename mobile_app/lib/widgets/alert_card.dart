import 'package:flutter/material.dart';
import '../models/alert.dart';
import '../theme/app_theme.dart';

class AlertCard extends StatelessWidget {
  final Alert alert;
  final String? imageUrl;
  final VoidCallback onTap;

  const AlertCard({
    super.key,
    required this.alert,
    required this.imageUrl,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final statusColor = AppColors.forStatus(alert.status);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.circular(radiusMd),
          border: Border.all(
            color: alert.isNew ? statusColor.withValues(alpha: 0.35) : AppColors.line,
          ),
          boxShadow: cardShadow(),
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(radiusMd),
          child: Material(
            color: Colors.transparent,
            child: InkWell(
              onTap: onTap,
              child: IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // A coloured spine instead of a dot -- reads at a
                    // glance from across a room, which is the point of a
                    // triage list.
                    Container(width: 4, color: statusColor),
                    Padding(
                      padding: const EdgeInsets.all(11),
                      child: Hero(
                        tag: 'alert-image-${alert.id}',
                        child: _Thumbnail(imageUrl: imageUrl),
                      ),
                    ),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.only(right: 12, top: 11, bottom: 11),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    'HUMAN DETECTED',
                                    style: TextStyle(
                                      color: statusColor,
                                      fontFamily: AppFonts.display,
                                      fontWeight: FontWeight.w700,
                                      fontSize: 13,
                                      letterSpacing: 0.2,
                                    ),
                                  ),
                                ),
                                Text(
                                  _relativeTime(alert.createdAtLocal),
                                  style: const TextStyle(
                                    color: AppColors.dim,
                                    fontFamily: AppFonts.data,
                                    fontSize: 10.5,
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 5),
                            Text.rich(
                              TextSpan(
                                children: [
                                  TextSpan(
                                    text: '${alert.personCount} ',
                                    style: const TextStyle(
                                      fontFamily: AppFonts.display,
                                      fontWeight: FontWeight.w700,
                                      fontSize: 14,
                                      color: AppColors.text,
                                    ),
                                  ),
                                  TextSpan(
                                    text: 'person${alert.personCount == 1 ? '' : 's'} · ',
                                    style: const TextStyle(
                                        fontFamily: AppFonts.body, fontSize: 12.5, color: AppColors.text),
                                  ),
                                  TextSpan(
                                    text: '${alert.confidence.toStringAsFixed(0)}%',
                                    style: const TextStyle(
                                      fontFamily: AppFonts.data,
                                      fontSize: 12.5,
                                      color: AppColors.textDim,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(height: 6),
                            Row(
                              children: [
                                Icon(
                                  alert.hasLocation
                                      ? Icons.location_on_rounded
                                      : Icons.location_off_rounded,
                                  size: 12,
                                  color: AppColors.dim,
                                ),
                                const SizedBox(width: 3),
                                Expanded(
                                  child: Text(
                                    alert.hasLocation
                                        ? '${alert.lat!.toStringAsFixed(4)}, ${alert.lon!.toStringAsFixed(4)}'
                                        : 'no GPS lock',
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                        color: AppColors.dim, fontFamily: AppFonts.data, fontSize: 10.5),
                                  ),
                                ),
                                const SizedBox(width: 6),
                                _SourceChip(alert: alert),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  static String _relativeTime(DateTime t) {
    final ago = DateTime.now().difference(t);
    if (ago.inSeconds < 60) return '${ago.inSeconds}s ago';
    if (ago.inMinutes < 60) return '${ago.inMinutes}m ago';
    if (ago.inHours < 24) return '${ago.inHours}h ago';
    return '${ago.inDays}d ago';
  }
}

class _Thumbnail extends StatelessWidget {
  final String? imageUrl;
  const _Thumbnail({required this.imageUrl});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(radiusSm),
      child: Container(
        width: 60,
        height: 60,
        decoration: BoxDecoration(border: Border.all(color: AppColors.line)),
        child: imageUrl == null
            ? const ColoredBox(
                color: AppColors.surfaceSunken,
                child: Icon(Icons.image_not_supported_outlined, color: AppColors.dim, size: 20),
              )
            : Image.network(
                imageUrl!,
                fit: BoxFit.cover,
                errorBuilder: (_, __, ___) => const ColoredBox(
                  color: AppColors.surfaceSunken,
                  child: Icon(Icons.broken_image_outlined, color: AppColors.dim, size: 20),
                ),
                loadingBuilder: (context, child, progress) {
                  if (progress == null) return child;
                  return const ColoredBox(
                    color: AppColors.surfaceSunken,
                    child: Center(
                      child: SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 1.8, color: AppColors.cyan),
                      ),
                    ),
                  );
                },
              ),
      ),
    );
  }
}

class _SourceChip extends StatelessWidget {
  final Alert alert;
  const _SourceChip({required this.alert});

  @override
  Widget build(BuildContext context) {
    final label = alert.isFromMesh
        ? 'LORA · ${alert.route ?? '?'} · ${alert.hopsOrZero}H'
        : 'CAMERA';
    final color = alert.isFromMesh ? AppColors.violet : AppColors.cyan;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(5),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontFamily: AppFonts.data,
          fontSize: 9,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.2,
        ),
      ),
    );
  }
}
