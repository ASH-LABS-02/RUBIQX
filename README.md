# RUBIQX · ARGUS

ARGUS is an autonomous search-and-rescue (SAR) research prototype developed for SIH'26 (Problem Statement 26177). It combines monocular visual SLAM, edge-optimised object detection models, and alert delivery (Wi‑Fi with LoRa fallback) to detect survivors and hazards and notify rescue teams. This repository contains prototypes, trained models, dataset artifacts, and documentation used for the project.

Status
- Research/prototype: SLAM and detection models demonstrated on laptop hardware; onboard integration and field testing are in development.

Highlights
- Trained YOLOv11 nano models for hazard detection (fire, smoke, crack, person) and occlusion-aware person detection.
- Monocular GPS‑denied visual mapping prototype using ORB‑SLAM3 (laptop camera → ORB‑SLAM3 client in Ubuntu/WSL2).
- Blender mission visualisations and evaluation tools for occluded-person detection (WiderPerson layout).

Stack
- Language(s): Python (primary), Dart (mobile app), HTML (dashboard), C++ (ORB‑SLAM3 client), MATLAB (evaluation)
- Frameworks / tools: Flask (camera server), ORB‑SLAM3 (visual mapping), Ultralytics YOLO (model training/export)
- Notable artifacts: PyTorch `.pt` models (YOLOv11), ONNX export, ncnn export directories for Pi deployment

Repository layout
```text
SLAM/                    Windows camera server + ORB‑SLAM3 client and patches
occluded_dataset/        WiderPerson images, annotations, MATLAB evaluator
ml-models/               Trained detection models and export helpers
simulation/blender/      Blender mission visualisations (simulation)
docs/                    Architecture, hardware abstraction, alert schema
templates/               Flask templates and dashboard assets
app.py                   Flask-based alert server and dashboard prototype
run_inference.py         Example inference script (connects camera → model)
lora_*.py                LoRa uplink/bridge scripts and utilities
start_dashboard.sh       Launch helper for the dashboard
```

How it fits together
- The laptop camera server (SLAM/src/camera_server.py) streams MJPEG frames over HTTP. ORB‑SLAM3 (Ubuntu/WSL2) consumes the stream to build a sparse visual map and provide camera pose. Detection models run on captured frames (prototype on laptop GPU; planned: Raspberry Pi 5 with ncnn). When detections are confirmed they are packaged into an alert (Wi‑Fi preferred; LoRa fallback) for a Flutter rescue app.

Quickstart — SLAM prototype (short)
1) On Windows: start the camera server
```powershell
cd <path-to-clone>/SLAM
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python src\camera_server.py
```
Server endpoints:
- http://localhost:5000/ — health
- http://<windows-host>:5000/video — MJPEG stream

2) On Ubuntu/WSL2: run ORB‑SLAM3 (after applying patch)
```bash
# In your ORB_SLAM3 checkout
git apply /path/to/RUBIQX/SLAM/patches/orb-slam3-changes.patch
# build ORB-SLAM3 per its instructions
WINDOWS_HOST=$(ip route | awk '/default/ {print $3; exit}')
./live_mono Vocabulary/ORBvoc.txt /path/to/RUBIQX/SLAM/config/LaptopCamera.yaml "http://$WINDOWS_HOST:5000/video"
```

Testing models (local)
```bash
pip install ultralytics
yolo predict model=ml-models/disaster-mlmodel.pt source=path/to/image.jpg
```
Export for Raspberry Pi / ncnn
```bash
yolo export model=ml-models/disaster-mlmodel.pt format=ncnn
```

Datasets and attribution
- occluded_dataset/ contains WiderPerson-derived annotations and MATLAB evaluation code. All dataset ownership and license terms belong to the original authors (see docs and occluded_dataset/README.md). Cite: Zhang et al., "WiderPerson: A Diverse Dataset for Dense Pedestrian Detection in the Wild", IEEE TMM (2020).

Limitations & next steps
- No flight tests or autonomous flight code included. The SLAM prototype is monocular and scale-ambiguous.
- No end-to-end onboard integration linking SLAM → detection → alert on the Pi; performance on Pi (frame rate, latency) is unmeasured.
- Planned: survivor projection (bearing + range), payload-drop safety workflow, thermal/acoustic sensing, and field trials.

How to contribute
- File issues for bugs or enhancements. Use PRs with a clear description and tests where possible. Sensitive model weights or dataset licensing issues: check docs/ and respect original dataset licenses.

Contact / Team
RUBIQX — SIH'26 team by ASH-LABS-02. See project files and issues for ongoing work.

License & citation
- This repository bundles third‑party artifacts (WiderPerson, ORB‑SLAM3). Follow their license terms. Provide academic citation where required.
