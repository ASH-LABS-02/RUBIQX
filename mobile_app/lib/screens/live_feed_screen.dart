import 'package:flutter/material.dart';
import '../services/alert_service.dart';
import '../services/connection_manager.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_banner.dart';
import '../widgets/mjpeg_view.dart';
import '../widgets/viewfinder_frame.dart';

class LiveFeedScreen extends StatelessWidget {
  final AlertService alertService;
  final ConnectionManager connection;

  const LiveFeedScreen({super.key, required this.alertService, required this.connection});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        ConnectionBanner(connection: connection),
        Expanded(
          child: ListenableBuilder(
            listenable: connection,
            builder: (context, _) {
              final url = alertService.videoFeedUrl;
              if (url == null) {
                return const Center(
                  child: Padding(
                    padding: EdgeInsets.all(28),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.wifi_off_rounded, color: AppColors.dim, size: 40),
                        SizedBox(height: 14),
                        Text(
                          'NO CONNECTION',
                          style: TextStyle(
                            color: AppColors.textDim,
                            fontFamily: AppFonts.display,
                            fontWeight: FontWeight.w700,
                            fontSize: 13,
                            letterSpacing: 0.6,
                          ),
                        ),
                        SizedBox(height: 8),
                        Text(
                          'Live video needs a local or cloud link — it cannot '
                          'travel over LoRa (far too little bandwidth for video). '
                          'Connect to the GCS hotspot or WiFi to view it.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: AppColors.dim, fontFamily: AppFonts.body, fontSize: 12, height: 1.4),
                        ),
                      ],
                    ),
                  ),
                );
              }
              return Padding(
                padding: const EdgeInsets.all(14),
                child: ViewfinderFrame(
                  child: MjpegView(key: ValueKey(url), url: url),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}
