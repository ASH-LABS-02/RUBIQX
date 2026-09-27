import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/alert.dart';
import 'connection_manager.dart';

/// Talks to the gateway's `/api/alerts*` routes (Drone-model/app.py on the
/// GCS Pi). Every call goes through [ConnectionManager.activeBaseUrl], so
/// callers never need to know or care whether that's the local hotspot or
/// a cloud relay -- if neither is reachable, calls simply fail and callers
/// fall back to whatever they already have cached.
class AlertService {
  final ConnectionManager connection;
  AlertService(this.connection);

  Future<List<Alert>> fetchAlerts({String? since}) async {
    final base = connection.activeBaseUrl;
    if (base == null) throw const ConnectionUnavailable();

    final uri = Uri.parse('$base/api/alerts').replace(
      queryParameters: since != null ? {'since': since} : null,
    );
    final res = await http.get(uri).timeout(const Duration(seconds: 8));
    if (res.statusCode != 200) {
      throw Exception('GET /api/alerts -> ${res.statusCode}');
    }
    final list = jsonDecode(res.body) as List<dynamic>;
    return list.map((e) => Alert.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<Alert> updateStatus(String alertId, String status, String? by) async {
    final base = connection.activeBaseUrl;
    if (base == null) throw const ConnectionUnavailable();

    final res = await http
        .post(
          Uri.parse('$base/api/alerts/$alertId/status'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'status': status, if (by != null) 'by': by}),
        )
        .timeout(const Duration(seconds: 8));
    if (res.statusCode != 200) {
      throw Exception('POST alert status -> ${res.statusCode}: ${res.body}');
    }
    return Alert.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  /// The image/video base URL to resolve a relative `image_url` against.
  String? resolveMediaUrl(String? relativeUrl) {
    final base = connection.activeBaseUrl;
    if (base == null || relativeUrl == null) return null;
    return '$base$relativeUrl';
  }

  String? get videoFeedUrl {
    final base = connection.activeBaseUrl;
    return base == null ? null : '$base/video_feed';
  }

  /// Live push of new/updated alerts over Server-Sent Events. Reconnects
  /// with backoff on any drop -- a phone moving through a building loses
  /// and regains WiFi constantly, and an "emergency alert" feature that
  /// silently stops working after one hiccup is worse than useless.
  ///
  /// Framing matches `/api/alerts/stream` exactly: lines starting with `:`
  /// are comments/heartbeats and are ignored; a `data: <json>` line is one
  /// event, terminated by a blank line per the SSE spec.
  Stream<Alert> liveStream() {
    late StreamController<Alert> controller;
    http.Client? client;
    bool cancelled = false;
    int backoffSeconds = 1;

    Future<void> connectLoop() async {
      while (!cancelled) {
        final base = connection.activeBaseUrl;
        if (base == null) {
          await Future.delayed(const Duration(seconds: 3));
          continue;
        }

        client = http.Client();
        try {
          final request = http.Request('GET', Uri.parse('$base/api/alerts/stream'));
          final response = await client!.send(request).timeout(const Duration(seconds: 10));
          if (response.statusCode != 200) {
            throw Exception('stream -> ${response.statusCode}');
          }
          backoffSeconds = 1; // connected: reset backoff

          var buffer = '';
          await for (final chunk in response.stream.transform(utf8.decoder)) {
            if (cancelled) break;
            buffer += chunk;
            while (buffer.contains('\n\n')) {
              final idx = buffer.indexOf('\n\n');
              final rawEvent = buffer.substring(0, idx);
              buffer = buffer.substring(idx + 2);
              for (final line in rawEvent.split('\n')) {
                if (line.startsWith('data:')) {
                  final payload = line.substring(5).trim();
                  try {
                    final json = jsonDecode(payload) as Map<String, dynamic>;
                    controller.add(Alert.fromJson(json));
                  } catch (_) {
                    // malformed event -- drop it, keep the connection alive
                  }
                }
                // lines starting with ':' are heartbeats/comments, ignored
              }
            }
          }
        } catch (_) {
          // fall through to reconnect below
        } finally {
          client?.close();
        }

        if (cancelled) break;
        await Future.delayed(Duration(seconds: backoffSeconds));
        backoffSeconds = (backoffSeconds * 2).clamp(1, 30);
      }
    }

    controller = StreamController<Alert>(
      onListen: () => unawaited(connectLoop()),
      onCancel: () {
        cancelled = true;
        client?.close();
      },
    );
    return controller.stream;
  }
}

/// Thrown when no connection (local or cloud) is currently reachable.
/// Callers should catch this specifically to show cached data rather than
/// a generic error.
class ConnectionUnavailable implements Exception {
  const ConnectionUnavailable();
  @override
  String toString() => 'No connection to the ground station (local or cloud)';
}
