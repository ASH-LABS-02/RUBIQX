#!/usr/bin/env python3
"""
Synthetic test script to verify model loading, warm-up, and latency on Raspberry Pi 5.
"""

import time
import numpy as np
import cv2
from pathlib import Path
from ultralytics import YOLO


def create_test_image(filename="test_sample.jpg"):
    """Create a synthetic 640x640 image with simple geometric patterns."""
    img = np.zeros((640, 640, 3), dtype=np.uint8)
    # Background gradient
    for y in range(640):
        img[y, :, 0] = int(y / 640 * 120) + 40
        img[y, :, 1] = int((640 - y) / 640 * 100) + 50
        img[y, :, 2] = 80

    # Draw a simulated figure (head, torso, legs)
    cv2.circle(img, (320, 200), 30, (200, 180, 160), -1)  # Head
    cv2.rectangle(img, (290, 230), (350, 420), (50, 50, 200), -1)  # Torso
    cv2.rectangle(img, (290, 420), (315, 560), (30, 30, 100), -1)  # Left leg
    cv2.rectangle(img, (325, 420), (350, 560), (30, 30, 100), -1)  # Right leg
    cv2.imwrite(filename, img)
    print(f"[*] Created synthetic test image: {filename}")
    return filename


def run_test(model_path="best.pt"):
    print(f"\n=======================================================")
    print(f" Testing YOLO11 Model: {model_path}")
    print(f"=======================================================")

    test_img = create_test_image("test_sample.jpg")

    print("[*] Loading model into memory...")
    t0 = time.perf_counter()
    model = YOLO(model_path)
    load_time = (time.perf_counter() - t0) * 1000
    print(f"[+] Model loaded in {load_time:.1f}ms")

    # Warm-up run
    print("[*] Running warm-up inference...")
    _ = model.predict(test_img, imgsz=640, device="cpu", verbose=False)

    # Benchmark multiple runs
    iterations = 5
    print(f"[*] Running {iterations} benchmark passes...")
    latencies = []
    for i in range(iterations):
        t_start = time.perf_counter()
        results = model.predict(
            test_img,
            imgsz=640,
            conf=0.25,
            device="cpu",
            verbose=False,
        )
        latency = (time.perf_counter() - t_start) * 1000
        latencies.append(latency)
        print(f"    Pass {i+1}: {latency:.1f} ms")

    avg_latency = sum(latencies) / len(latencies)
    est_fps = 1000.0 / avg_latency if avg_latency > 0 else 0.0
    print(f"[+] Average Inference Latency: {avg_latency:.1f} ms (~{est_fps:.1f} FPS)")

    # Save output visualization
    res = results[0]
    output_path = Path("runs/detect/test_sample_output.jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    res.save(filename=str(output_path))
    print(f"[+] Saved test result to: {output_path}")
    print(f"[+] Detected objects: {len(res.boxes)}")
    for box in res.boxes:
        cls_id = int(box.cls[0].item())
        cls_name = res.names[cls_id]
        conf = float(box.conf[0].item())
        print(f"    - {cls_name}: confidence {conf:.2f}, bbox: {box.xyxy[0].tolist()}")

    print("=======================================================\n")


if __name__ == "__main__":
    run_test()
