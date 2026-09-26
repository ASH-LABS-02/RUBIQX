# ARGUS Detection Models

Both models are **YOLOv11 nano** (Ultralytics), chosen to run on edge hardware.

> **Folder note:** both `.pt` files currently sit inside a folder named `disaster-mlmodel.pt/`.
> Move them up one level into `ml-models/` so the paths below are correct.

## 1. Hazard model: `disaster-mlmodel.pt`

| | |
|---|---|
| **Detects** | fire, smoke, crack, person |
| **Training data** | ~48,000 disaster images (Roboflow) |
| **mAP@50** | 58.8% |
| **Precision** | 61.8% |
| **Recall** | 62.3% |
| **F1** | 62.0% |
| **Status** | ✅ trained and validated · 🔧 ncnn export for Raspberry Pi 5 |

## 2. Occluded-person model: `occluded-mlmodel.pt`

| | |
|---|---|
| **Detects** | person, including partly hidden people |
| **Training data** | WiderPerson (see [`../occluded_dataset/`](../occluded_dataset/)): 8,000 train / 1,000 val images |
| **Label mapping** | classes 1 (pedestrians), 2 (riders), 3 (partially-visible persons), 5 (crowd) → `person`; class 4 (ignore regions) dropped |
| **Hardware** | Trained on an NVIDIA RTX 4060 (8GB) |
| **Metrics** | *Not yet reported. Add mAP@50, precision and recall from the validation run.* |
| **Status** | ✅ trained · 🔧 ncnn export for Raspberry Pi 5 |

### Planned comparison

The key test for this model: **recall on occluded people, stock `yolo11n` vs this model**,
on the WiderPerson validation set (class 3, partially-visible persons). That number shows
whether occlusion training actually finds more hidden survivors.

## Quick test

```bash
pip install ultralytics
yolo predict model=ml-models/disaster-mlmodel.pt source=path/to/image.jpg
```

## Export for Raspberry Pi 5

```bash
yolo export model=ml-models/disaster-mlmodel.pt format=ncnn
```

## Not yet measured

- Frame rate on the Raspberry Pi 5
- Performance on real drone footage (not yet tested)
