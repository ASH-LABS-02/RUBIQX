"""Raw visual-SLAM input and geolocation provenance, without synthetic positions."""
import time
from dataclasses import asdict
import cv2

def register_mapping_routes(app, worker, alerts):
    from flask import Response, jsonify

    @app.route('/video_feed_raw')
    def slam_camera():
        def frames():
            sequence = -1
            while worker.running:
                with worker.frame_ready:
                    worker.frame_ready.wait_for(lambda: worker.frame_sequence != sequence or not worker.running, timeout=2)
                    if not worker.running:
                        return
                    if sequence == worker.frame_sequence:
                        continue
                    sequence = worker.frame_sequence
                    frame = worker.latest_raw_frame_bgr
                    active = worker.camera_active
                if not active or frame is None:
                    continue
                # 16:9 dimensions preserve the current USB sensor's aspect ratio.
                h, w = frame.shape[:2]
                if w * 9 != h * 16:
                    return  # Require a matching calibration instead of stretching images.
                small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
                ok, jpeg = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    data = jpeg.tobytes()
                    yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data + b'\r\n'
                time.sleep(0.08)
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @app.route('/api/survivors/locations')
    def survivor_locations():
        with alerts.lock:
            records = [asdict(a) for a in alerts.alerts[-40:]][::-1]
        # Existing lat/lon records include drone-position proxies. Without an
        # explicit georeferencing method they must never become survivor pins.
        return jsonify({'survivors': [{
            'id': a['id'], 'confidence': a['confidence'], 'created_at': a['created_at'],
            'person_count': a.get('person_count', 1), 'image_url': a.get('image_url'),
            'source': a.get('source'), 'status': a.get('status'),
            'location': {'available': False, 'reason': 'Camera detection has no measured geographic position'},
            'observer_location': {'latitude': a.get('observer_lat'), 'longitude': a.get('observer_lon'),
                                  'accuracy': a.get('observer_accuracy_m'), 'source': 'google_wifi'},
        } for a in records], 'georeferenced_survivors': 0})
