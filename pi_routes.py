"""Serve actual laptop SLAM on the Pi dashboard. Input arrives over SSH only."""
import json
import time
import re
from pathlib import Path


def read_published_state(directory):
    try:
        path = Path(directory) / 'current.json'
        age = time.time() - path.stat().st_mtime
        data = json.loads(path.read_text())
        if not isinstance(data, dict): raise ValueError('invalid state')
        total_age = age + float(data.get('age_seconds', 0))
        if age < -5 or total_age > 5:
            raise ValueError('stale')
        if data.get('available'):
            data['age_seconds'] = max(0, total_age)
        return data
    except (OSError, ValueError, TypeError):
        return {'available': False, 'status': 'publisher_offline',
                'reason': 'Waiting for live SLAM from the laptop',
                'points': [], 'keyframes': [], 'camera': None}


def register_slam_routes(app, worker=None):
    from flask import jsonify, send_from_directory, abort, request, Response
    root = Path(app.root_path)
    live = root / '.slam-live'

    @app.get('/slam-api/state')
    def slam_state():
        response = jsonify(read_published_state(live))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/slam-api/frame.jpg')
    def slam_frame():
        if not read_published_state(live).get('available'):
            abort(404)
        frame_id=request.args.get('frame','')
        if not re.fullmatch(r'\d+-\d+',frame_id): abort(404)
        response = send_from_directory(live, 'frame-'+frame_id+'.jpg', max_age=0)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/slam/')
    def slam_page():
        response = send_from_directory(root / 'slam_dist', 'index.html', max_age=0)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/slam/assets/<path:filename>')
    def slam_assets(filename):
        return send_from_directory(root / 'slam_dist' / 'assets', filename, max_age=31536000)

    if worker is not None:
        @app.get('/video_feed_raw')
        def slam_input():
            import cv2
            def frames():
                sequence=-1
                while worker.running:
                    with worker.frame_ready:
                        worker.frame_ready.wait_for(lambda: worker.frame_sequence!=sequence or not worker.running,timeout=2)
                        if not worker.running: return
                        if sequence==worker.frame_sequence: continue
                        sequence=worker.frame_sequence
                        frame=worker.latest_raw_frame_bgr
                        active=worker.camera_active
                    if not active or frame is None: continue
                    h,w=frame.shape[:2]
                    if w*9!=h*16: return
                    small=cv2.resize(frame,(640,360),interpolation=cv2.INTER_AREA)
                    ok,jpeg=cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,85])
                    if ok:
                        body=jpeg.tobytes()
                        yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body+b'\r\n'
                    time.sleep(.08)
            return Response(frames(),mimetype='multipart/x-mixed-replace; boundary=frame',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
