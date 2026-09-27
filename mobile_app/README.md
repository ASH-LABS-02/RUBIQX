# SAR Rescue — mobile app

Flutter app for the rescue team: emergency alerts the instant the drone or
ground camera detects a person, live video, a location map, and a working
answer to "what happens with no internet" that doesn't pretend a phone can
receive LoRa directly (it can't — no phone has a LoRa radio).

## The architecture question, answered

**A phone cannot receive a LoRa packet.** What "send it through LoRa if
there's no internet" actually means, physically: the drone's detection
travels over the LoRa mesh to the **Ground Station Pi** (which does have a
LoRa radio — see `../sar/` and `../sar_link_demo_codex`), and the Pi is
what the phone talks to. The only thing that changes with or without
internet is the *last hop* between the phone and that Pi:

```
        LoRa mesh                    local WiFi hotspot / LAN
Drone ─────────────► Ground Station ─────────────────────────► Phone
                          Pi                 (no internet needed)
                           │
                           └──────────────► internet ──────► Phone
                              (optional cloud relay, not built)
```

This is exactly the project's existing offline-first design, one hop
longer. `ConnectionManager` (`lib/services/connection_manager.dart`) is
what implements this: it prefers the local link (lower latency, zero
external dependency) and only falls back to a cloud URL if one has been
configured in Settings. **No cloud relay is built in this prototype** —
the app is a complete, working system on the local path alone; the cloud
slot exists so a hosted relay can be added later without changing the app.

## What's built

- **Emergency alerts, live**: a persistent SSE connection
  (`lib/services/alert_service.dart`) to the gateway's `/api/alerts/stream`
  turns every new detection into a full-screen, sound+vibration local
  notification within about a second of it happening.
- **Live video**: `lib/widgets/mjpeg_view.dart` is a hand-written MJPEG
  parser matched exactly to the gateway's own multipart framing — no
  third-party package of uncertain quality.
- **Alert feed, detail, and actions**: acknowledge / mark resolved / false
  alarm, each attributed to the rescuer's name (Settings), each pushed back
  to the gateway and re-broadcast to every other connected phone.
- **Schematic map**: plots detections by relative lat/lon with no basemap
  tiles — a real map needs internet to fetch tiles (or an offline cache
  this prototype doesn't build), which would quietly break the one thing
  this feature exists for.
- **"Navigate here"**: hands the alert's coordinates to whatever maps app
  is installed, when GPS was available at detection time.
- **Persisted settings**: GCS address, optional cloud relay address,
  rescuer name.

## What's explicitly not built, and why

- **No cloud relay.** Building and hosting one needs real infrastructure
  (a public server, TLS, some auth) that isn't a 7-day-hackathon-prototype
  task. The app is fully functional without it.
- **No Firebase Cloud Messaging** (wake-from-fully-killed push). What's
  built fires the instant the app is open or backgrounded-but-not-killed,
  via a live SSE connection — genuinely real-time, just not
  survive-a-phone-reboot durable. FCM needs a Firebase project tied to a
  Google account only the team can create.
- **No real basemap tiles.** See the map section above — this is a
  deliberate choice, not an oversight.
- **GPS on alerts is null until the drone's GPS/Pixhawk link is wired into
  the detection pipeline** (`Drone-model/app.py` on the Pi doesn't have
  that yet). The app already handles it correctly — "no GPS lock" is shown
  honestly rather than a fabricated position.

## Backend

The gateway this app talks to lives in `../Drone-model/app.py` (deployed on
the GCS Pi). See its `MODEL_REGISTRY` comment block and the
`# Emergency alert gateway` section for the schema and every endpoint:
`/api/alerts`, `/api/alerts/stream`, `/api/alerts/<id>/status`,
`/api/alerts/ingest` (for the LoRa mesh to feed in a detection later,
identically to a local one), `/api/health`, `/video_feed`.

## Running it

```bash
flutter pub get
flutter run                 # needs a connected device or emulator
flutter build apk --debug   # produces build/app/outputs/flutter-apk/app-debug.apk
```

Default GCS address is `http://192.168.1.9:5000` (this project's bench
Pi) — change it in Settings if yours differs; it's saved and reused.

For a phone to actually reach the Pi with no internet, it needs to join
whatever local network the Pi is on. This prototype assumes that network
already exists (a shared router, or the Pi configured as its own WiFi
access point) — setting the Pi up as an access point itself is worth doing
before a field demo and isn't done here.
