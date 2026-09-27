/// How the app is currently reaching the ground control station.
///
/// A phone has no LoRa radio -- it physically cannot receive a mesh packet
/// directly. What "offline-first" means for this app is: the Ground
/// Station Pi is the thing that receives LoRa, and the phone reaches the
/// *Pi* over whichever network is available. [local] covers a disaster
/// scenario with zero internet -- the phone joins the Pi's own WiFi
/// hotspot (or a shared LAN) and talks to it directly by IP. [cloud]
/// covers a rescuer away from the GCS with normal signal, via an optional
/// relay -- not built in this prototype, just a slot to wire in later.
enum ConnectionMode {
  /// Reaching the GCS Pi directly over a local network/hotspot. No
  /// internet required. This is the primary, always-available path.
  local,

  /// Reaching a cloud relay over the internet. Optional; only used if a
  /// cloud URL is configured in Settings AND local isn't reachable.
  cloud,

  /// Neither reachable. The app shows cached/last-known data only.
  offline,
}

extension ConnectionModeLabel on ConnectionMode {
  String get label => switch (this) {
        ConnectionMode.local => 'Local (GCS direct)',
        ConnectionMode.cloud => 'Internet (cloud relay)',
        ConnectionMode.offline => 'Offline',
      };

  String get shortLabel => switch (this) {
        ConnectionMode.local => 'LOCAL',
        ConnectionMode.cloud => 'CLOUD',
        ConnectionMode.offline => 'OFFLINE',
      };
}
