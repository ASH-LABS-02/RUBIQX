#!/usr/bin/env python3
"""
Export YOLO11 model to ONNX or NCNN for accelerated inference on Raspberry Pi 5.
"""

import argparse
from pathlib import Path
import sys

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export YOLO11 model to ONNX or NCNN for Raspberry Pi 5"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="best.pt",
        help="Path to source .pt model (default: best.pt)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="onnx",
        choices=["onnx", "ncnn", "tflite", "torchscript"],
        help="Target export format (default: onnx; 'ncnn' is also very fast on ARM)",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Image size for export (default: 640)",
    )
    parser.add_argument(
        "--half",
        action="store_true",
        help="Export in FP16 half precision",
    )
    parser.add_argument(
        "--simplify",
        action="store_true",
        default=True,
        help="Simplify ONNX graph using onnxslim (default: True)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if YOLO is None:
        print("[!] Error: 'ultralytics' is not installed.")
        sys.exit(1)

    model_file = Path(args.model)
    if not model_file.exists():
        print(f"[!] Error: Model file '{model_file}' not found.")
        sys.exit(1)

    print(f"[*] Loading model from {model_file}...")
    model = YOLO(str(model_file))

    print(f"[*] Exporting to format: {args.format.upper()} (imgsz={args.imgsz}, half={args.half})...")
    export_path = model.export(
        format=args.format,
        imgsz=args.imgsz,
        half=args.half,
        simplify=args.simplify,
    )
    print(f"[+] Model successfully exported to: {export_path}")


if __name__ == "__main__":
    main()
