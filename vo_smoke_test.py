"""Standalone smoke test for VisualOdometryWorker's pose-recovery math --
no camera needed. Builds two synthetic frames related by a KNOWN pure
translation (a textured image shifted sideways) and checks that:
  1. _process() runs end-to-end without raising
  2. tracking_ok goes True with a healthy matched-feature count
  3. the pipeline actually reaches pose recovery on a clean synthetic pair

Run from ~/Drone-model on the Pi: python3 vo_smoke_test.py

This does NOT validate real-world axis/sign conventions (whether a real
rightward camera pan produces a positive or negative x_m step) -- that
needs the actual camera reconnected, panned deliberately one way, with the
resulting trail checked against the real motion.
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as sar_app  # noqa: E402 -- path insert must run first


def make_textured_frame(w=640, h=480, seed=0):
    rng = np.random.default_rng(seed)
    # Random blobs give ORB plenty of corners to find -- a blank/gradient
    # image would legitimately fail to track, which is correct behaviour
    # for the real thing, not something to paper over here.
    img = np.full((h, w), 30, dtype=np.uint8)
    for _ in range(400):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = int(rng.integers(3, 9))
        val = int(rng.integers(120, 255))
        cv2.circle(img, (int(x), int(y)), r, val, -1)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def shift_frame(frame, dx_px):
    h, w = frame.shape[:2]
    m = np.float32([[1, 0, dx_px], [0, 1, 0]])
    return cv2.warpAffine(frame, m, (w, h), borderMode=cv2.BORDER_REPLICATE)


def main():
    base = make_textured_frame(seed=1)
    shifted = shift_frame(base, dx_px=15)

    vo = sar_app.VisualOdometryWorker(detection_worker=None)
    print(f"[*] calibrated intrinsics: {vo.calibrated} (expect False -- no camera_calibration.json in this test dir)")

    vo._process(base)
    print(f"[*] after frame 1: tracking_ok={vo.tracking_ok} matched={vo.matched_features} "
          f"(expected: not yet tracking, no prior frame to match against)")

    vo._process(shifted)
    print(f"[*] after frame 2: tracking_ok={vo.tracking_ok} matched={vo.matched_features}")
    print(f"[*] recovered relative position: x_m={vo._x_m:.3f} y_m={vo._y_m:.3f}")

    assert vo.frames_processed == 2, f"expected 2 frames processed, got {vo.frames_processed}"
    if not vo.tracking_ok:
        print("[!] FAIL: tracking did not lock on with a clean synthetic pair -- matcher/threshold problem")
        sys.exit(1)
    if vo.matched_features < sar_app.VO_MIN_MATCHES:
        print(f"[!] FAIL: only {vo.matched_features} matched features, below VO_MIN_MATCHES={sar_app.VO_MIN_MATCHES}")
        sys.exit(1)

    print("[+] PASS: pipeline runs end-to-end and locks tracking on a clean synthetic pair.")


if __name__ == "__main__":
    main()
