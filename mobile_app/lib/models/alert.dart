/// One emergency detection, exactly mirroring the shape the ground-station
/// gateway API returns (`Alert` in Drone-model/app.py). Whether it came
/// from the GCS's own camera or was relayed in over the LoRa mesh from the
/// flying drone, it arrives here in this one shape -- the app never has to
/// know which.
class Alert {
  final String id;
  final double createdAt; // unix seconds
  final double confidence; // 0-100
  final int personCount;
  final String source; // "camera-direct" | "lora-mesh"
  final String? imageUrl; // relative to the gateway base URL
  final double? lat;
  final double? lon;
  final String? route; // "DIRECT" | "RELAY", mesh alerts only
  final int? hops;
  final double? rssiDbm;
  final String status; // new | acknowledged | resolved | false_alarm
  final String? acknowledgedBy;
  final double? acknowledgedAt;
  final bool? thermalVerified; // camera-direct only; null for lora-mesh alerts

  const Alert({
    required this.id,
    required this.createdAt,
    required this.confidence,
    required this.personCount,
    required this.source,
    this.imageUrl,
    this.lat,
    this.lon,
    this.route,
    this.hops,
    this.rssiDbm,
    this.status = 'new',
    this.acknowledgedBy,
    this.acknowledgedAt,
    this.thermalVerified,
  });

  factory Alert.fromJson(Map<String, dynamic> json) {
    return Alert(
      id: json['id'] as String,
      createdAt: (json['created_at'] as num).toDouble(),
      confidence: (json['confidence'] as num).toDouble(),
      personCount: json['person_count'] as int,
      source: json['source'] as String? ?? 'camera-direct',
      imageUrl: json['image_url'] as String?,
      lat: (json['lat'] as num?)?.toDouble(),
      lon: (json['lon'] as num?)?.toDouble(),
      route: json['route'] as String?,
      hops: json['hops'] as int?,
      rssiDbm: (json['rssi_dbm'] as num?)?.toDouble(),
      status: json['status'] as String? ?? 'new',
      acknowledgedBy: json['acknowledged_by'] as String?,
      acknowledgedAt: (json['acknowledged_at'] as num?)?.toDouble(),
      thermalVerified: json['thermal_verified'] as bool?,
    );
  }

  Alert copyWith({String? status, String? acknowledgedBy, double? acknowledgedAt}) {
    return Alert(
      id: id,
      createdAt: createdAt,
      confidence: confidence,
      personCount: personCount,
      source: source,
      imageUrl: imageUrl,
      lat: lat,
      lon: lon,
      route: route,
      hops: hops,
      rssiDbm: rssiDbm,
      status: status ?? this.status,
      acknowledgedBy: acknowledgedBy ?? this.acknowledgedBy,
      acknowledgedAt: acknowledgedAt ?? this.acknowledgedAt,
      thermalVerified: thermalVerified,
    );
  }

  DateTime get createdAtLocal =>
      DateTime.fromMillisecondsSinceEpoch((createdAt * 1000).round()).toLocal();

  bool get hasLocation => lat != null && lon != null;

  bool get isFromMesh => source == 'lora-mesh';

  bool get isNew => status == 'new';

  /// True hops away from the ground station -- 0/null both read as
  /// "direct", since a camera-direct alert has no hop count at all.
  int get hopsOrZero => hops ?? 0;
}
