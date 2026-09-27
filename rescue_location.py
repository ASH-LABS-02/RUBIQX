"""Volatile phone fixes and honest distance estimates. No simulated fallback."""
import math
import threading
import time


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_fix(data, now=None):
    now = time.time() if now is None else now
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    lat, lon, accuracy, timestamp = (data.get(k) for k in ('latitude', 'longitude', 'accuracy', 'timestamp'))
    if not number(lat) or not -90 <= lat <= 90 or not number(lon) or not -180 <= lon <= 180:
        raise ValueError('Valid latitude and longitude are required')
    if not number(accuracy) or accuracy < 0:
        raise ValueError('Horizontal accuracy in meters is required')
    if not number(timestamp) or not now - 30 <= timestamp / 1000 <= now + 5:
        raise ValueError('Phone fix must be at most 30 seconds old; check the phone clock')
    return {'latitude': lat, 'longitude': lon, 'accuracy': accuracy, 'timestamp': timestamp,
            'source': 'phone_location', 'simulated': False}


def distance_m(a, b):
    lat1, lat2 = math.radians(a['latitude']), math.radians(b['latitude'])
    dlat = lat2 - lat1
    dlon = math.radians(b['longitude'] - a['longitude'])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(max(0, min(1, h))))


class TeamLocation:
    def __init__(self):
        self.lock = threading.Lock()
        self.fix = None

    def update(self, data):
        fix = validate_fix(data)
        with self.lock:
            if self.fix and fix['timestamp'] <= self.fix['timestamp']:
                raise ValueError('Out-of-order phone fix')
            self.fix = fix
        return fix

    def clear(self):
        with self.lock:
            self.fix = None

    def status(self):
        with self.lock:
            fix = dict(self.fix) if self.fix else None
        if fix is None:
            return {'available': False, 'reason': 'Phone GPS has not been shared'}
        age = time.time() - fix['timestamp'] / 1000
        return {**fix, 'available': -5 <= age <= 30, 'age_seconds': round(age, 1),
                'reason': None if -5 <= age <= 30 else 'Phone GPS is stale'}


def register_location_routes(app, location_worker):
    from flask import request, jsonify
    team = TeamLocation()

    @app.route('/api/team/position', methods=['POST', 'DELETE'])
    def team_position():
        if request.method == 'DELETE':
            team.clear()
            return jsonify({'ok': True})
        if request.content_length and request.content_length > 4096:
            return jsonify({'error': 'Position payload too large'}), 413
        try:
            team.update(request.get_json(silent=True))
        except ValueError as error:
            return jsonify({'error': str(error)}), 400
        return jsonify({'ok': True})

    @app.route('/api/rescue/positions')
    def rescue_positions():
        loc = location_worker.status()
        age = loc.get('seconds_since_fix')
        drone = {'available': False, 'source': 'google_wifi', 'reason': loc.get('last_error') or 'Waiting for Google Wi-Fi location'}
        if loc.get('available') and number(age):
            drone = {'available': age <= 90, 'source': 'google_wifi', 'simulated': False,
                     'latitude': loc['lat'], 'longitude': loc['lon'], 'accuracy': loc.get('accuracy_m'),
                     'timestamp': (time.time() - age) * 1000, 'age_seconds': age,
                     'reason': None if age <= 90 else 'Drone Wi-Fi location is stale'}
        phone = team.status()
        distance = None
        uncertainty = None
        if drone['available'] and phone['available']:
            distance = round(distance_m(drone, phone), 1)
            if number(drone['accuracy']):
                uncertainty = drone['accuracy'] + phone['accuracy']
        return jsonify({'drone': drone, 'team': phone, 'distance_m': distance,
                        'combined_accuracy_m': uncertainty, 'estimated': True,
                        'distance_kind': 'straight_line_surface', 'server_time': time.time() * 1000})
