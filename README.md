# RUBIQX | ARGUS
## Rescue intelligence when connectivity is unreliable

**A human-in-the-loop search-and-rescue research prototype:** detect visible people and hazards, deliver compact alerts over a relay radio link, and give operators a shared view of the evidence.

Built for SIH problem statement 26177. The contribution is system integration across perception, constrained communications and operator workflows—not a new detector or SLAM algorithm.

> **Prototype boundary:** no verified autonomous flight, thermal sensing, metric survivor localization or field-rescue effectiveness is claimed. RGB cannot see through rubble. Model confidence is not the probability that a survivor is present.

## What is implemented—and what is not

| Component | Repository evidence | Current boundary |
|---|---|---|
| Person detection | YOLO inference, checkpoint/export assets, configurable confidence | Disaster-domain accuracy and occlusion improvement need controlled evaluation |
| Hazard detection | Separate fire/smoke inference worker | Upstream model metrics are not ARGUS field results |
| Alert delivery | Binary packets, SPI driver, ESP32 relay firmware, deduplication, hop/TTL handling | Radio range, delivery rate and end-to-end latency need recorded bench/field tests |
| Operator interface | Flask dashboard, alert history, Flutter app source | Human review required; no authentication or production hardening |
| Visual mapping | Separate ORB-SLAM3 camera-stream integration | Camera-only prototype; unknown metric scale; not connected to flight control |
| Dashboard odometry | Experimental frame-to-frame ORB pose recovery | Arbitrary display units, no loop closure, disabled by default |
| Mission visualization | Blender assets and illustrative map | Not evidence of autonomous navigation or measured search coverage |
| Autonomous flight / payload release | Roadmap only | Not implemented or flight-validated |

Evidence is classified as **implemented**, **automated-test verified**, or **hardware/field measured**. These are not interchangeable. See [validation](docs/VALIDATION.md).

## Why this approach is useful

- **Small alerts on a constrained link:** person count and confidence can travel without streaming video over LoRa.
- **Explicit uncertainty:** missing coordinates stay unknown; simulated positions remain separate.
- **Operator continuity:** local dashboard, alert persistence and relay metadata support review when infrastructure is limited.
- **Testable boundaries:** camera, inference, radio and localization can be evaluated separately before integration.

Benefits such as faster rescue, longer range or fewer missed survivors are hypotheses until measured—not headline percentages.

## Run without hardware or model weights

Python 3.11+ recommended. From the repository root:

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell instead:
# .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-review.txt
python -m pytest -q
```

Start an explicitly offline review session:

```bash
# Linux/macOS
ARGUS_OFFLINE=1 python app.py
```

```powershell
# Windows PowerShell
$env:ARGUS_OFFLINE="1"
python app.py
```

Open [dashboard](http://127.0.0.1:5000/), [vision](http://127.0.0.1:5000/ml), or [ground station](http://127.0.0.1:5000/gcs).

Review mode does not initialize radios, load model checkpoints, open a camera, or start Wi-Fi geolocation. The video is a labelled placeholder, not recorded evidence. A few browser assets may still depend on internet access.

## Run with a camera / inference

1. Install `requirements.txt` in the environment.
2. Remove `ARGUS_OFFLINE` or set it to `0`.
3. Ensure the selected checkpoint/export exists locally. Only load checkpoints from a trusted source.
4. Run `python app.py`. Model-loading failures remain visible; no missing model is silently downloaded.
5. Pi camera, NCNN and SPI access require platform-specific dependencies and device permissions; see [hardware setup](docs/hardware_abstraction.md).

The default listener is **127.0.0.1**. For a trusted bench LAN, explicitly set `ARGUS_HOST=0.0.0.0`. The prototype has unauthenticated control/ingestion endpoints: do not expose it to the internet. `ARGUS_PORT` overrides port 5000.

For a saved image/video:
```bash
python run_inference.py --model best.pt --source path/to/video.mp4 --save
```

[Model inventory](ml-models/README.md) distinguishes active models from other artifacts. [SLAM setup](SLAM/README.md) is a separate process, not part of the dashboard startup.

## Judge-facing demo: evidence, not promises

1. Show the capability table and explain which inputs are real.
2. On a real camera, show a visible person detection and an empty-scene negative example.
3. With radios connected, trace one packet ID from origin through relay to receipt; show a disconnected-link case too.
4. Show the operator alert, acknowledge it, restart and confirm persistence.
5. Present the separate SLAM experiment with its unknown-scale limitation.
6. Show the automated test output and measured results, if available. Do not substitute Blender footage for algorithm evidence.

See [demo and evidence checklist](docs/VALIDATION.md) for the measurements needed before claiming improvement.

## Repository map

| Path | Purpose |
|---|---|
| `app.py`, `templates/` | Dashboard, inference workers, alert API |
| `sar/packet.py`, `sar/mesh.py`, `sar/radio/` | Radio protocol and transport |
| `sar/visual_odometry.py` | Isolated experimental relative VO |
| `firmware/esp32_relay/` | ESP32 radio firmware |
| `mobile_app/` | Flutter operator client |
| `SLAM/` | ORB-SLAM3 camera adapter and viewer patch |
| `tests/` | Hardware-free regression suite |
| `tools/evaluate_model.py` | Evaluation report generator with model hash |
| `simulation/` | Illustrative Blender assets |

## Attribution and ownership

Ultralytics supplies the detection framework; ORB-SLAM3 supplies the SLAM engine. Dataset and pretrained-model rights remain with their respective owners. The fire/smoke model source is recorded in [model documentation](ml-models/README.md). A repository-wide license must be selected by the owners; this revision does not grant rights to third-party datasets or weights.

**Team:** RUBIQX. **Platform:** ARGUS.
