import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import '../theme/app_theme.dart';

/// Renders the GCS's MJPEG stream (`/video_feed` in Drone-model/app.py).
///
/// There's no mature, actively-maintained Flutter package for MJPEG-over-
/// multipart that's worth trusting sight-unseen for a hackathon demo, and
/// the format itself is simple enough to parse directly against exactly
/// what our own backend emits: a `--frame` boundary line, a
/// `Content-Type: image/jpeg` header, a blank line, the raw JPEG bytes,
/// then `\r\n` and the next boundary -- repeated forever. This widget
/// streams raw bytes, finds each frame's
/// boundary by byte search (not text decoding -- the payload is binary),
/// and hands each complete JPEG to Image.memory.
class MjpegView extends StatefulWidget {
  final String url;
  const MjpegView({super.key, required this.url});

  @override
  State<MjpegView> createState() => _MjpegViewState();
}

class _MjpegViewState extends State<MjpegView> {
  http.Client? _client;
  StreamSubscription<List<int>>? _sub;
  Uint8List? _currentFrame;
  String? _error;
  int _framesReceived = 0;

  static final _boundary = '--frame'.codeUnits;
  static final _headerEnd = [13, 10, 13, 10]; // \r\n\r\n

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void didUpdateWidget(MjpegView old) {
    super.didUpdateWidget(old);
    if (old.url != widget.url) {
      _teardown();
      _connect();
    }
  }

  @override
  void dispose() {
    _teardown();
    super.dispose();
  }

  void _teardown() {
    _sub?.cancel();
    _client?.close();
  }

  void _connect() {
    setState(() => _error = null);
    final buffer = BytesBuilder(copy: false);

    _client = http.Client();
    _client!.send(http.Request('GET', Uri.parse(widget.url))).then((response) {
      if (response.statusCode != 200) {
        throw Exception('video_feed -> ${response.statusCode}');
      }
      _sub = response.stream.listen(
        (chunk) {
          buffer.add(chunk);
          _drainFrames(buffer);
        },
        onError: (e) {
          if (mounted) setState(() => _error = e.toString());
        },
        onDone: () {
          if (mounted) setState(() => _error ??= 'stream ended');
        },
        cancelOnError: true,
      );
    }).catchError((e) {
      if (mounted) setState(() => _error = e.toString());
    });
  }

  /// Pulls as many complete frames as are currently sitting in [buffer],
  /// displays the LAST one (dropping any others), and leaves whatever
  /// trailing partial frame remains for the next chunk. Always rendering
  /// only the newest frame keeps the view live even if the phone's network
  /// falls behind the stream's rate.
  void _drainFrames(BytesBuilder buffer) {
    var bytes = buffer.toBytes();
    Uint8List? latest;

    while (true) {
      final headerStart = _indexOf(bytes, _boundary, 0);
      if (headerStart == -1) break;
      final dataStart = _indexOf(bytes, _headerEnd, headerStart);
      if (dataStart == -1) break;
      final imageStart = dataStart + _headerEnd.length;

      final nextBoundary = _indexOf(bytes, _boundary, imageStart);
      if (nextBoundary == -1) break; // frame not fully received yet

      // Trim the trailing \r\n that precedes the next boundary marker.
      var imageEnd = nextBoundary;
      while (imageEnd > imageStart &&
          (bytes[imageEnd - 1] == 13 || bytes[imageEnd - 1] == 10)) {
        imageEnd--;
      }
      if (imageEnd > imageStart) {
        latest = Uint8List.sublistView(bytes, imageStart, imageEnd);
      }
      bytes = Uint8List.sublistView(bytes, nextBoundary);
    }

    if (bytes.length != buffer.length) {
      buffer.clear();
      buffer.add(bytes);
    }

    if (latest != null && mounted) {
      _framesReceived++;
      setState(() => _currentFrame = latest);
    }
  }

  static int _indexOf(Uint8List haystack, List<int> needle, int from) {
    final limit = haystack.length - needle.length;
    for (var i = from; i <= limit; i++) {
      var match = true;
      for (var j = 0; j < needle.length; j++) {
        if (haystack[i + j] != needle[j]) {
          match = false;
          break;
        }
      }
      if (match) return i;
    }
    return -1;
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return _StatusOverlay(
        icon: Icons.videocam_off_outlined,
        message: 'Live feed unavailable\n$_error',
        actionLabel: 'Retry',
        onAction: _connect,
      );
    }
    if (_currentFrame == null) {
      return const _StatusOverlay(
        icon: Icons.videocam_outlined,
        message: 'Connecting to live feed…',
      );
    }
    return Stack(
      fit: StackFit.expand,
      children: [
        Image.memory(
          _currentFrame!,
          gaplessPlayback: true, // don't flash blank between frames
          fit: BoxFit.contain,
          errorBuilder: (_, __, ___) => const _StatusOverlay(
            icon: Icons.broken_image_outlined,
            message: 'Bad frame received, waiting for next…',
          ),
        ),
        // A moving frame count is the simplest proof the stream is genuinely
        // live and not just showing one frozen image -- worth more than it
        // looks like on a field tool where "is this actually updating?" is
        // a real question under a bad connection.
        Positioned(
          left: 10,
          bottom: 10,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.55),
              borderRadius: BorderRadius.circular(999),
              border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 5,
                  height: 5,
                  decoration: const BoxDecoration(color: AppColors.red, shape: BoxShape.circle),
                ),
                const SizedBox(width: 5),
                Text(
                  'LIVE · $_framesReceived',
                  style: const TextStyle(
                    color: Colors.white70,
                    fontFamily: AppFonts.data,
                    fontSize: 9.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _StatusOverlay extends StatelessWidget {
  final IconData icon;
  final String message;
  final String? actionLabel;
  final VoidCallback? onAction;

  const _StatusOverlay({
    required this.icon,
    required this.message,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: AppColors.surfaceSunken,
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: AppColors.dim, size: 34),
            const SizedBox(height: 10),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Text(
                message,
                textAlign: TextAlign.center,
                style: const TextStyle(
                    color: AppColors.dim, fontFamily: AppFonts.body, fontSize: 12, height: 1.4),
              ),
            ),
            if (actionLabel != null) ...[
              const SizedBox(height: 14),
              OutlinedButton(onPressed: onAction, child: Text(actionLabel!)),
            ],
          ],
        ),
      ),
    );
  }
}
