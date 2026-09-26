# Hardware Abstraction: From Laptop Prototype to Field Drone

ARGUS is built so each capability can move from a laptop test, to a simulation,
to real drone hardware, with the software interfaces kept the same. This document
shows where every component stands today and what replaces it at each stage.

**Status key:** ✅ Demonstrated · 🔧 In development · 📋 Planned

## 1. Stage overview

| Stage | What it is | Status |
|---|---|---|
| **A. Laptop prototype** | Models trained on an RTX 4060; ORB-SLAM3 tracking a laptop webcam stream | ✅ |
| **B. Simulation** | Blender scenes that show the mission concept (not physics or flight simulation) | ✅ (visual only) |
| **C. Prototype drone** (~₹51k) | Raspberry Pi 5 on an F450 frame with Pixhawk, NEO-6M GPS, LoRa | 🔧 / 📋 |
| **D. Field build** (~₹1.9L) | Qualcomm Dragonwing RB3 Gen 2, thermal camera, 650-class frame | 📋 |

## 2. Component mapping

| Capability | A. Laptop prototype (today) | B. Simulation | C. Prototype drone | D. Field build |
|---|---|---|---|---|
| **Compute** | Laptop CPU / RTX 4060 | — | Raspberry Pi 5 (8GB), ncnn | Qualcomm RB3 Gen 2 (12 TOPS NPU) |
| **Hazard detection** | YOLOv11 `disaster-mlmodel.pt` ✅ | Shown in Blender | Same model, ncnn export 🔧 | Same model, NPU export 📋 |
| **Occluded-person detection** | YOLOv11 `occluded-mlmodel.pt` ✅ | Shown in Blender | Same model, ncnn export 🔧 | Same model, NPU export 📋 |
| **Camera** | Laptop webcam, 640×480 | Blender camera | Pi Camera Module 3 / USB webcam | 12MP RGB on 2-axis gimbal |
| **Mapping (SLAM)** | ORB-SLAM3 monocular ✅ | `argus_computed_slam_v004.blend` | ORB-SLAM3 + IMU (visual-inertial) 📋 | Same, with calibrated camera 📋 |
| **Altitude** | — | — | TF-Luna 1D rangefinder | TF-Luna |
| **Position** | — | — | NEO-6M GPS | M10 GPS + compass |
| **Heading** (for survivor location) | — | — | QMC5883L magnetometer 📋 | Built into M10 module |
| **Thermal** | — | — | MLX90640 (32×24) 📋 | 256×192 radiometric module 📋 |
| **Flight control** | — | — | Pixhawk 2.4.8 + ArduPilot (MAVLink) 📋 | Pixhawk 6C 📋 |
| **Primary alert link** | — | — | Onboard Wi-Fi → rescue app 🔧 | Onboard Wi-Fi 📋 |
| **Fallback alert link** | — | — | SX1278 LoRa → ESP32 receiver 🔧 | 865 MHz LoRa (India licence-free band) 📋 |
| **Rescuer interface** | — | — | Flutter rescue app 🔧 | Flutter rescue app 📋 |

## 3. What stays the same across stages

- **Detection output:** every stage produces the same detection record (class, confidence,
  bounding box, timestamp). See [`alert_schema.md`](alert_schema.md).
- **Alert format:** the Wi-Fi and LoRa messages are identical at stages C and D.
- **SLAM input:** ORB-SLAM3 reads a camera stream URL or device, so the laptop MJPEG stream
  (`SLAM/src/camera_server.py`) is replaced by the drone camera without changing the tracker.

## 4. Known gaps between stages

Stating these plainly:

- **The Blender scenes are visualisations.** They show what the mission looks like; they do
  not simulate flight physics, sensor noise or real detection output.
- **Monocular SLAM has no true scale.** The laptop map is correct in shape but not in metres.
  Fusing the IMU (visual-inertial mode) is the planned fix.
- **The models have not yet run on the Pi.** Frame rate on the Pi 5 is not yet measured.
- **The laptop camera calibration is approximate** (zero distortion coefficients in
  `SLAM/config/LaptopCamera.yaml`). The drone camera must be calibrated before flight.
- **Survivor GPS is the drone's GPS today.** Projecting the survivor's own position needs heading
  (magnetometer) and range (TF-Luna); see [`architecture.md`](architecture.md#survivor-location).
