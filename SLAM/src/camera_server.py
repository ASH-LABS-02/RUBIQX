from flask import Flask, Response
import cv2

CAMERA_INDEX = 0
WIDTH = 640
HEIGHT = 480
FPS = 30

app = Flask(__name__)

camera = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
camera.set(cv2.CAP_PROP_FPS, FPS)

if not camera.isOpened():
    raise RuntimeError(
        "Could not open camera. Close Camera, Teams, Zoom, or try CAMERA_INDEX = 1."
    )


def frames():
    while True:
        ok, frame = camera.read()
        if not ok:
            continue

        ok, jpeg = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, 85],
        )
        if not ok:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + jpeg.tobytes()
            + b"\r\n"
        )


@app.route("/")
def index():
    return "Camera server is running. Open /video for the stream."


@app.route("/video")
def video():
    return Response(
        frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


if __name__ == "__main__":
    print("Camera stream: http://0.0.0.0:5000/video")
    app.run(host="0.0.0.0", port=5000, threaded=True)
