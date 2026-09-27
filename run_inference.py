#!/usr/bin/env python3
"""
Real-time Drone Person Detection on Raspberry Pi 5
Supports PyTorch (.pt) and ONNX (.onnx) models with live camera, video file, or RTSP streaming.
"""

import argparse
import os
import sys
import time
from pathlib import Path
import cv2

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None


def setup_display_env():
    """Ensure DISPLAY and WAYLAND_DISPLAY are set if active desktop sockets exist."""
    if "DISPLAY" not in os.environ and os.path.exists("/tmp/.X11-unix/X0"):
        os.environ["DISPLAY"] = ":0"
    if "WAYLAND_DISPLAY" not in os.environ and os.path.exists("/run/user/1000/wayland-0"):
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
        os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")


def is_gui_available():
    """Check if graphical display environment is available."""
    setup_display_env()
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run YOLO11 Drone Person Detection on Raspberry Pi 5"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="best.pt",
        help="Path to model file (.pt, .onnx, or ncnn folder)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="0",
        help="Input source: '0' for webcam, path to video/image file, or RTSP stream URL",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Inference image resolution (default: 640; try 320 or 480 for higher FPS on Pi)",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.35,
        help="Confidence threshold for detection (default: 0.35)",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=0.45,
        help="NMS IoU threshold (default: 0.45)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show live video window (requires GUI display)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save annotated video/image output",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="runs/detect",
        help="Directory to save output files (default: runs/detect)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum number of frames to process",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        help="Device to use for inference ('cpu' on Raspberry Pi 5)",
    )
    return parser.parse_args()


def process_image(model, image_path, args, out_dir):
    """Run detection on a single image file."""
    print(f"[*] Processing image: {image_path}")
    t0 = time.perf_counter()
    results = model.predict(
        source=image_path,
        conf=args.conf,
        iou=args.iou,
        imgsz=args.imgsz,
        device=args.device,
        verbose=False,
    )
    dt = (time.perf_counter() - t0) * 1000
    res = results[0]
    num_persons = len(res.boxes)
    print(f"[+] Detections: {num_persons} person(s) found in {dt:.1f}ms")

    annotated = res.plot()

    if args.save:
        out_path = out_dir / f"pred_{Path(image_path).name}"
        cv2.imwrite(str(out_path), annotated)
        print(f"[+] Saved annotated image to: {out_path}")

    if args.show and is_gui_available():
        cv2.imshow("YOLO11 Drone Person Detection", annotated)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def process_video_or_stream(model, source, args, out_dir):
    """Run detection on video file, camera stream, or RTSP."""
    source_input = int(source) if source.isdigit() else source

    print(f"[*] Opening video source: {source_input}")
    cap = cv2.VideoCapture(source_input)

    if not cap.isOpened():
        print(f"[!] Error: Unable to open video source '{source}'")
        sys.exit(1)

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    fps_in = cap.get(cv2.CAP_PROP_FPS)
    if fps_in <= 0 or fps_in > 120:
        fps_in = 30.0

    print(f"[*] Source properties: {width}x{height} @ {fps_in:.1f} FPS")

    writer = None
    if args.save:
        src_name = Path(str(source)).stem if not str(source).isdigit() else f"cam_{source}"
        out_video_path = out_dir / f"pred_{src_name}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_video_path), fourcc, fps_in, (width, height))
        print(f"[*] Saving output video to: {out_video_path}")

    can_show = args.show and is_gui_available()
    if args.show and not can_show:
        print("[!] Warning: GUI display not detected. Disabling live preview window (--show).")

    frame_count = 0
    start_time = time.perf_counter()
    fps_ema = 0.0

    print("[*] Starting inference loop. Press Ctrl+C or 'q' in window to quit.")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[*] End of video stream / input source reached.")
                break

            t_frame_start = time.perf_counter()

            results = model.predict(
                source=frame,
                conf=args.conf,
                iou=args.iou,
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )

            res = results[0]
            annotated_frame = res.plot()
            num_persons = len(res.boxes)

            t_frame_end = time.perf_counter()
            frame_time = t_frame_end - t_frame_start
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0.0
            fps_ema = instant_fps if fps_ema == 0.0 else (0.9 * fps_ema + 0.1 * instant_fps)

            # Draw FPS and count badge on frame
            fps_text = f"FPS: {fps_ema:.1f} | Persons: {num_persons} | {args.imgsz}x{args.imgsz}"
            cv2.putText(
                annotated_frame,
                fps_text,
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            if writer:
                writer.write(annotated_frame)

            if can_show:
                cv2.imshow("YOLO11 Drone Person Detection (RPi 5)", annotated_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    print("[*] Quit requested by user.")
                    break

            frame_count += 1
            if frame_count % 30 == 0 or frame_count == 1:
                print(
                    f"[{frame_count:04d}] Inference: {frame_time*1000:.1f}ms "
                    f"({fps_ema:.1f} FPS) | Detected: {num_persons} person(s)"
                )

            if args.max_frames and frame_count >= args.max_frames:
                print(f"[*] Reached max-frames limit ({args.max_frames}).")
                break

    except KeyboardInterrupt:
        print("\n[*] Interrupted by user.")
    finally:
        total_time = time.perf_counter() - start_time
        avg_fps = frame_count / total_time if total_time > 0 else 0.0
        cap.release()
        if writer:
            writer.release()
        if can_show:
            cv2.destroyAllWindows()
        print(f"[+] Completed: {frame_count} frames processed in {total_time:.2f}s (Average: {avg_fps:.1f} FPS).")


def process_picamera(model, args, out_dir):
    """Run real-time detection directly on Raspberry Pi Camera via Picamera2."""
    try:
        from picamera2 import Picamera2
    except ImportError:
        print("[!] Error: 'picamera2' is not available. Ensure python3-picamera2 is installed.")
        sys.exit(1)

    print("[*] Initializing Raspberry Pi Camera (Picamera2)...")
    picam2 = Picamera2()
    config = picam2.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
    picam2.configure(config)
    picam2.start()

    width, height = 640, 480
    fps_in = 30.0

    writer = None
    if args.save:
        out_video_path = out_dir / "pred_picamera.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_video_path), fourcc, fps_in, (width, height))
        print(f"[*] Saving output video to: {out_video_path}")

    can_show = args.show and is_gui_available()
    if args.show and not can_show:
        print("[!] Warning: GUI display not detected. Disabling live preview window (--show).")

    frame_count = 0
    start_time = time.perf_counter()
    fps_ema = 0.0

    print("[*] Pi Camera started. Running detection loop. Press Ctrl+C or 'q' to stop.")
    try:
        while True:
            # Capture RGB frame from hardware ISP
            frame_rgb = picam2.capture_array()
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

            t_frame_start = time.perf_counter()

            results = model.predict(
                source=frame_bgr,
                conf=args.conf,
                iou=args.iou,
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )

            res = results[0]
            annotated_frame = res.plot()
            num_persons = len(res.boxes)

            t_frame_end = time.perf_counter()
            frame_time = t_frame_end - t_frame_start
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0.0
            fps_ema = instant_fps if fps_ema == 0.0 else (0.9 * fps_ema + 0.1 * instant_fps)

            # Draw FPS and count badge
            fps_text = f"PiCam FPS: {fps_ema:.1f} | Persons: {num_persons} | {args.imgsz}x{args.imgsz}"
            cv2.putText(
                annotated_frame,
                fps_text,
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            if writer:
                writer.write(annotated_frame)

            if can_show:
                cv2.imshow("Raspberry Pi Camera - Person Detection", annotated_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    print("[*] Quit requested by user.")
                    break

            frame_count += 1
            if frame_count % 30 == 0 or frame_count == 1:
                print(
                    f"[{frame_count:04d}] PiCam Inference: {frame_time*1000:.1f}ms "
                    f"({fps_ema:.1f} FPS) | Detected: {num_persons} person(s)"
                )

            if args.max_frames and frame_count >= args.max_frames:
                print(f"[*] Reached max-frames limit ({args.max_frames}).")
                break

    except KeyboardInterrupt:
        print("\n[*] Interrupted by user.")
    finally:
        total_time = time.perf_counter() - start_time
        avg_fps = frame_count / total_time if total_time > 0 else 0.0
        picam2.stop()
        picam2.close()
        if writer:
            writer.release()
        if can_show:
            cv2.destroyAllWindows()
        print(f"[+] Pi Camera finished: {frame_count} frames in {total_time:.2f}s (Average: {avg_fps:.1f} FPS).")


def main():
    args = parse_args()

    if YOLO is None:
        print("[!] Error: 'ultralytics' is not installed. Please run dependency installation first.")
        sys.exit(1)

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"[!] Error: Model file '{model_path}' does not exist.")
        sys.exit(1)

    out_dir = Path(args.output_dir)
    if args.save:
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Loading model: {model_path} (Format: {model_path.suffix or 'folder'})")
    model = YOLO(str(model_path), task="detect")

    source_lower = str(args.source).lower()
    if source_lower in ("picam", "picamera", "rpicam", "csi"):
        process_picamera(model, args, out_dir)
        return

    # Detect if source is image or video/stream
    image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    source_path = Path(args.source)

    if source_path.is_file() and source_path.suffix.lower() in image_exts:
        process_image(model, args.source, args, out_dir)
    else:
        # If source is 0, check if V4L2 yields frames or fallback to Pi Camera
        if args.source == "0":
            test_cap = cv2.VideoCapture(0)
            ret, _ = test_cap.read()
            test_cap.release()
            if not ret:
                print("[*] Standard V4L2 /dev/video0 not streaming. Switching to Raspberry Pi Camera (Picamera2)...")
                process_picamera(model, args, out_dir)
                return
        process_video_or_stream(model, args.source, args, out_dir)


if __name__ == "__main__":
    main()

