"""One-time camera calibration for the Visual Odometry worker in app.py.

Run this ON THE PI with the camera physically attached, holding a printed
OpenCV chessboard (9x6 inner corners -- e.g. the standard one at
https://github.com/opencv/opencv/blob/4.x/doc/pattern.png) in view. Move
it around the frame -- tilted, near corners, far away -- for about 20
good captures; it captures automatically when it sees a clean board and
prints a running count.

Writes camera_calibration.json next to this script. VisualOdometryWorker
in app.py loads it automatically on the next restart and reports
`calibrated: true` in /api/vo/status instead of falling back to an
assumed-FOV estimate. Measure your actual printed square size with a
ruler and set SQUARE_SIZE_M below before running -- the fx/fy this
produces scale with that number.
"""
import json
import time

import cv2
import numpy as np
from picamera2 import Picamera2

CHESSBOARD = (9, 6)    # inner corners, not squares
SQUARE_SIZE_M = 0.025  # measure your printed board's actual square size and edit this
TARGET_SAMPLES = 20


def main():
    picam2 = Picamera2()
    config = picam2.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
    picam2.configure(config)
    picam2.start()
    time.sleep(1)

    objp = np.zeros((CHESSBOARD[0] * CHESSBOARD[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:CHESSBOARD[0], 0:CHESSBOARD[1]].T.reshape(-1, 2) * SQUARE_SIZE_M

    objpoints, imgpoints = [], []
    gray = None
    print(f"[*] Show a {CHESSBOARD[0]}x{CHESSBOARD[1]}-corner chessboard to the camera.")
    print(f"[*] Capturing automatically when a clean corner set is found -- need {TARGET_SAMPLES}.")

    last_capture = 0.0
    try:
        while len(objpoints) < TARGET_SAMPLES:
            frame = picam2.capture_array()
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            found, corners = cv2.findChessboardCorners(gray, CHESSBOARD, None)

            if found and time.monotonic() - last_capture > 1.0:
                corners = cv2.cornerSubPix(
                    gray, corners, (11, 11), (-1, -1),
                    (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
                )
                objpoints.append(objp)
                imgpoints.append(corners)
                last_capture = time.monotonic()
                print(f"[+] Captured {len(objpoints)}/{TARGET_SAMPLES}")
    finally:
        picam2.stop()

    if gray is None or len(objpoints) < TARGET_SAMPLES:
        print("[!] Stopped early -- not enough samples, calibration not saved.")
        return

    h, w = gray.shape
    ok, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, (w, h), None, None)
    if not ok:
        print("[!] Calibration failed -- try again with more/better-spread samples.")
        return

    result = {
        "fx": float(K[0][0]), "fy": float(K[1][1]),
        "cx": float(K[0][2]), "cy": float(K[1][2]),
        "dist_coeffs": dist.flatten().tolist(),
        "resolution": [w, h],
        "samples": TARGET_SAMPLES,
        "calibrated_at": time.time(),
    }
    with open("camera_calibration.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"[+] Saved camera_calibration.json -- fx={result['fx']:.1f} fy={result['fy']:.1f}")
    print("[+] Restart app.py for the Visual Odometry worker to pick it up.")


if __name__ == "__main__":
    main()
