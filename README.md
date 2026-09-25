# RUBIQX

**Search-and-rescue perception prototypes for SIH'26.** RUBIQX combines an occlusion-focused pedestrian-detection dataset, a disaster-detection model artifact, and a GPS-denied monocular SLAM proof of concept for rapid experimentation in rescue robotics.

> **Current status:** research/prototype repository. The checked-in SLAM pipeline maps a laptop-camera stream with ORB-SLAM3; it does not fly a drone, detect survivors end-to-end, provide thermal sensing, or produce a dense 3D reconstruction.

## What is here

| Component | Purpose | Start here |
|---|---|---|
| [`SLAM/`](SLAM/) | Windows webcam → MJPEG stream → Ubuntu/WSL2 → ORB-SLAM3 monocular tracking and sparse map | [`SLAM/README.md`](SLAM/README.md) |
| [`occluded_dataset/`](occluded_dataset/) | WiderPerson images, annotations, splits, and MATLAB evaluation tools for dense pedestrian detection under occlusion | [`occluded_dataset/README.md`](occluded_dataset/README.md) |
| [`ml-models/`](ml-models/) | Trained disaster-detection model artifact (`disaster-mlmodel.pt`) | No inference wrapper is included yet |

## Fastest path: run the SLAM prototype

The SLAM prototype requires **two environments**:

- **Windows:** Python, Flask, and OpenCV for the camera server.
- **Ubuntu/WSL2:** a separately installed and built [ORB-SLAM3](https://github.com/UZ-SLAMLab/ORB_SLAM3) checkout with OpenCV, Pangolin, Eigen3, DBoW2, g2o, Boost.Serialization, and OpenSSL. This repository does not vendor those dependencies.

### 1. Start the Windows camera server

```powershell
cd <path-to-clone>\SLAM
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install flask opencv-python
python src\camera_server.py
```

The server exposes:

- `http://localhost:5000/` — health message
- `http://<windows-host>:5000/video` — multipart MJPEG stream

If the webcam cannot be opened, close Camera/Teams/Zoom or change `CAMERA_INDEX` in [`SLAM/src/camera_server.py`](SLAM/src/camera_server.py). The server requests 640×480 at 30 FPS and JPEG quality 85.

### 2. Apply the ORB-SLAM3 prototype patch

From the root of your ORB-SLAM3 checkout:

```bash
git apply /path/to/RUBIQX/SLAM/patches/orb-slam3-changes.patch
# Configure and build ORB-SLAM3 using its own build instructions.
```

The patch adds the `live_mono` executable and replaces the default current-camera viewer marker with a wireframe quadcopter marker. It is a source patch, not a standalone build system.

### 3. Start monocular tracking in Ubuntu/WSL2

```bash
cd /path/to/ORB_SLAM3
WINDOWS_HOST=$(ip route | awk '/default/ {print $3; exit}')
./live_mono \
  Vocabulary/ORBvoc.txt \
  /path/to/RUBIQX/SLAM/config/LaptopCamera.yaml \
  "http://$WINDOWS_HOST:5000/video"
```

The executable expects exactly three arguments:

```text
live_mono <vocabulary_file> <settings_file> <camera_url>
```

Press **Q**, **Esc**, or **Ctrl+C** to stop cleanly. When at least 10 frames have valid tracking, the prototype writes `LiveKeyFrameTrajectory.txt` in the current working directory.

### Throughput and tracking tuning

The checked-in camera profile is intentionally conservative:

- 640×480 input, 15 FPS SLAM configuration, 1,200 ORB features
- camera stream at 30 FPS with a one-frame capture buffer requested by the C++ client
- approximate pinhole calibration with zero distortion coefficients

For higher throughput, tune one variable at a time in [`SLAM/config/LaptopCamera.yaml`](SLAM/config/LaptopCamera.yaml): reduce `ORBextractor.nFeatures`, lower the camera resolution, or reduce the stream FPS. For better tracking quality, calibrate the camera instead of relying on the provisional values. Monocular scale remains unknown.

## Dataset evaluation

`occluded_dataset/` is the WiderPerson benchmark layout:

```text
occluded_dataset/
  Images/          13,382 images
  Annotations/     training/validation annotations
  Evaluation/      MATLAB evaluation code and validation metadata
  train.txt        training split
  val.txt          validation split
  test.txt         testing split
```

Annotations use `[class_label, x1, y1, x2, y2]` after the per-image count. Labels are:

| ID | Class |
|---:|---|
| 1 | pedestrians |
| 2 | riders |
| 3 | partially-visible persons |
| 4 | ignore regions |
| 5 | crowd |

Prediction files use one file per image with `[x1, y1, x2, y2, score]` after the detection count. The MATLAB evaluator reports **Recall**, **AP**, and **MR** for **Easy**, **Medium**, and **Hard** validation subsets at IoU 0.5.

To evaluate predictions:

1. Put a prediction directory under `occluded_dataset/Evaluation/`.
2. Ensure each prediction filename matches the image stem and follows the format above.
3. Edit `legend_name` and `pred_dir` in [`occluded_dataset/Evaluation/wider_eval.m`](occluded_dataset/Evaluation/wider_eval.m).
4. Run from the evaluation directory in MATLAB:

```matlab
cd occluded_dataset/Evaluation
wider_eval
```

The evaluator writes `<legend_name>_eval_result.txt` beside the prediction directory. See [`occluded_dataset/README.md`](occluded_dataset/README.md) for the original benchmark details and attribution.

## Repository layout

```text
.
├── SLAM/
│   ├── src/              Windows camera server and C++ ORB-SLAM3 client
│   ├── config/           provisional ORB-SLAM3 laptop-camera calibration
│   └── patches/          ORB-SLAM3 build/viewer modifications
├── occluded_dataset/
│   ├── Images/           WiderPerson image set
│   ├── Annotations/      ground-truth annotations
│   └── Evaluation/       MATLAB metrics and validation metadata
├── ml-models/            disaster-detection model artifact
└── README.md
```

## Limitations and next steps

- No top-level training or inference script currently connects `disaster-mlmodel.pt` to the camera or SLAM output.
- The SLAM prototype uses a laptop camera, approximate calibration, monocular scale, and a sparse map.
- Autonomous flight, obstacle avoidance, survivor confirmation, thermal sensing, and sensor fusion are outside the current implementation.
- ORB-SLAM3 and Pangolin are external dependencies; follow their licenses and build instructions.
- Dataset files retain the original WiderPerson ownership and terms. Cite the original work when using them.

## Citation and attribution

This repository includes the WiderPerson dataset. Cite:

> Zhang, S., Xie, Y., Wan, J., Xia, H., Li, S. Z., & Guo, G. (2020). *WiderPerson: A Diverse Dataset for Dense Pedestrian Detection in the Wild*. IEEE Transactions on Multimedia, 22(2), 380–393. DOI: [10.1109/TMM.2019.2929005](https://doi.org/10.1109/TMM.2019.2929005)

See the [official WiderPerson website](http://www.cbsr.ia.ac.cn/users/sfzhang/WiderPerson/) and the component READMEs for source-specific terms.

## Team

RUBIQX — SIH'26 team project by [ASH-LABS-02](https://github.com/ASH-LABS-02).
