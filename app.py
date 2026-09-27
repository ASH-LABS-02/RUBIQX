#!/usr/bin/env python3
"""
Flask Web Dashboard for Drone Person Detection on Raspberry Pi 5.
Streams live Pi Camera feed with real-time YOLO11 detection, telemetry, and interactive controls.
"""

import json
import math
import os
import queue
import subprocess
import sys
import time
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# Must be set before numpy/cv2 first touch a BLAS/OpenMP backend -- these
# read the env var once at native-library init, not dynamically. This
# process runs 4 real concurrent CV workers on only 4 physical cores
# (capture loop, person-detection inference, HazardWorker, VisualOdometry-
# Worker), and each one calls into OpenCV ops (resize, color-convert,
# JPEG encode/decode, ORB) that, left alone, individually fan out across
# ALL cores by default -- with 4 threads doing that at once, actual
# demand can hit 4x the machine's real capacity and everything thrashes
# instead of the 4 workers cleanly time-sharing 4 cores. Capping each
# call to 1 thread here (cv2.setNumThreads(1) below does the same for
# OpenCV specifically, as a second guarantee) turns "4 threads each
# fighting for all 4 cores" into "4 threads, each using roughly 1" --
# confirmed against cv2.getNumThreads() defaulting to 4 on this board;
# see the commit/session notes for the before/after inference-fps numbers
# this was verified against.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import cv2
import numpy as np
import psutil
import requests
from flask import Flask, Response, render_template, request, jsonify, send_from_directory

cv2.setNumThreads(1)

# LoRa uplink -- optional. This Pi's RA-02 is wired straight to its own SPI
# bus (no ESP32 on the drone side -- see tools/checkradio.py, the
# hardware-verified configuration), so the uplink used here is the direct-
# SPI one, not lora_uplink.py's ESP32-over-USB DroneLoRaLink (that one is
# for a Pi wired the other way; kept in the repo for that case, just not
# imported here). Import failure must degrade to "detections aren't
# leaving over LoRa", never to "the dashboard won't start".
try:
    from lora_uplink_direct import DirectRadioUplink
    HAS_LORA_UPLINK = True
except ImportError as e:
    DirectRadioUplink = None
    HAS_LORA_UPLINK = False
    print(f"[*] LoRa uplink not available ({e}) -- detections will not be sent over LoRa")

# Ultralytics & Model
from ultralytics import YOLO

# Picamera2
try:
    from picamera2 import Picamera2
    HAS_PICAM2 = True
except ImportError:
    HAS_PICAM2 = False

app = Flask(__name__)

RUNS_DIR = Path("runs/detect")
RUNS_DIR.mkdir(parents=True, exist_ok=True)

# Each exported model has a FIXED input shape baked into its graph at export
# time (NCNN and ONNX both do this; only the raw .pt is shape-flexible). The
# imgsz sent to model.predict() must match that shape, or Ultralytics either
# throws (ONNX) or silently re-letterboxes to the wrong size before feeding a
# network optimised for a different resolution (NCNN) -- which does not
# crash, but runs several times slower with no error to explain why.
#
# Binding imgsz to the model name here, and nowhere else, is what makes that
# whole bug class structurally impossible: there is no longer a resolution
# control that can disagree with the selected engine.
#
# Measured on this Pi 5 (device=cpu):
#   NCNN  320   40 ms/frame   ~25 fps   fastest
#   NCNN  416   69 ms/frame   ~14 fps   recommended -- real accuracy gain, still fluid
#   NCNN  480   91 ms/frame   ~11 fps   higher accuracy, still watchable
#   NCNN  640  247 ms/frame   ~4.0 fps  native training resolution -- best.pt was
#                                       trained at imgsz=640 on WiderPerson; this is
#                                       the only tier that matches it exactly, at a
#                                       real speed cost. Not the default: picking this
#                                       to chase accuracy is very likely to reintroduce
#                                       the "camera is lagging" complaint this file's
#                                       history already went through once.
#   PT    320  262 ms/frame  ~3.8 fps   reference only
#   ONNX  640  359 ms/frame  ~2.8 fps   reference only; this export is 640-only
#
# The trained checkpoint's own validation metrics (from best.pt's embedded
# train_metrics): precision 0.823, recall 0.652, mAP50 0.764, mAP50-95 0.484.
# ~35% of real people are missed even in the validation set's own conditions
# -- that ceiling comes from the training run (100 epochs, WiderPerson, from
# a yolo11s pretrained base) and no inference-side setting here changes it.
# Raising it for real needs more/better training, which isn't possible from
# this Pi (no dataset or GPU here -- training happened on a separate machine,
# see WiderPerson\YOLO\data.yaml in the checkpoint's recorded train_args).
MODEL_REGISTRY = {
    "best_ncnn_model_320": {"imgsz": 320, "label": "NCNN 320 — Fastest",              "engine": "NCNN"},
    "best_ncnn_model_416": {"imgsz": 416, "label": "NCNN 416 — Recommended",          "engine": "NCNN"},
    "best_ncnn_model_480": {"imgsz": 480, "label": "NCNN 480 — Higher Accuracy",      "engine": "NCNN"},
    "best_ncnn_model_640": {"imgsz": 640, "label": "NCNN 640 — Native (very slow)",   "engine": "NCNN"},
    "best.pt":              {"imgsz": 320, "label": "PyTorch — Reference (slow)", "engine": "PyTorch"},
    "best.onnx":             {"imgsz": 640, "label": "ONNX — Reference (slow)",    "engine": "ONNX"},
}
DEFAULT_MODEL = "best_ncnn_model_416"

CAMERA_STREAM_URL = os.environ.get("CAMERA_STREAM_URL", "").strip()
"""When set (e.g. to a phone's IP Webcam MJPEG URL), DetectionWorker reads
frames from this network stream instead of the Pi's own attached camera
module. Everything downstream -- HazardWorker, VisualOdometryWorker, the
dashboard, alerts -- reads DetectionWorker.latest_frame_bgr the same way
regardless of which source filled it, so nothing else needs to change."""

USB_CAMERA_DEVICE = os.environ.get("USB_CAMERA_DEVICE", "/dev/video0")
"""A UVC webcam plugged directly into the Pi's USB port (e.g. the Zebronics
Zeb-Live Pro used here) rather than the ribbon-cable camera module. Tried
automatically whenever this device node exists and CAMERA_STREAM_URL isn't
set -- see init_camera(). Same latest_frame_bgr contract as every other
source; nothing downstream needs to know a USB cam is active."""

STREAM_TARGET_FPS = 15.0
"""Soft cap on DetectionWorker._capture_loop's own rate. That loop runs
independently of YOLO inference (see its docstring), so left uncapped it
will composite+JPEG-encode as fast as the camera can deliver frames --
measured at up to ~18fps on 720p on this Pi 5, which pushed all 4 cores to
100% and dragged inference down to ~1.7fps (from ~9fps before the capture/
inference split existed at all). cv2.setNumThreads(1) below (avoiding each
of the 4 concurrent workers fanning its own calls across all 4 cores) plus
capping this loop's own resolution recovered some of that, but inference
still ran well under its pre-split rate even at a 15fps cap -- there's
real, not-fully-diagnosed contention beyond simple core-count math here
(possibly the ncnn Python binding not releasing the GIL for the duration
of predict(), which would serialize it against every other Python thread
regardless of free cores). 10fps is still clearly smoother than a feed
locked to inference speed; this is the knob to raise first if a future
profiling pass explains the remaining gap and frees up more give-back, and
to lower first if inference throughput still isn't enough."""

GOOGLE_GEOLOCATION_API_KEY = os.environ.get("GOOGLE_GEOLOCATION_API_KEY", "").strip()
"""When set, LocationWorker (below) periodically scans nearby WiFi access
points and sends them to Google's Geolocation API for a real position fix
-- tens-of-meters precision, no GPS hardware, no wiring risk. Chosen after
checking the free alternatives on this exact rig: Mozilla's public
geolocate API (would have been the no-signup option) is dead (404, live-
tested against this network); plain IP geolocation works but only resolved
to city level (Coimbatore, not this specific location) -- not what
"precise" means here. Requires the user's own Google Cloud API key with
the Geolocation API enabled; empty by default, in which case
/api/mission/position falls back to simulated_mission_position() exactly
as before -- this feature is additive, never a requirement."""
WIFI_INTERFACE = os.environ.get("WIFI_INTERFACE", "wlan0")
LOCATION_UPDATE_INTERVAL_S = 60.0  # position doesn't need to update faster than this, and Google's free tier has a daily request quota
LOCATION_RETRY_INTERVAL_S = 10.0  # after a failed fetch only -- see LocationWorker.run()

ALERTS_FILE = Path("alerts.jsonl")
ALERT_COOLDOWN_S = 8.0
"""Minimum gap between alerts. A person standing in frame at ~14fps would
otherwise generate one alert per frame -- this is the detection-persistence
/ cooldown behaviour the original project spec flagged as a future
enhancement. One real alert every 8s is plenty for a rescue team; an alert
storm is the surest way to get a genuine one ignored."""

MAX_ALERTS_IN_MEMORY = 500


@dataclass
class Alert:
    """One emergency detection, in the shape every consumer agrees on.

    Both a detection made by THIS Pi's own camera and one relayed in from
    the LoRa mesh (a drone with no direct link to a phone) land here through
    different paths but produce the same shape -- see `source`. The mobile
    app, and anything else downstream, only ever has to understand this one
    schema, never which subsystem produced it.
    """

    id: str
    created_at: float
    confidence: float
    person_count: int
    source: str                      # "camera-direct" | "lora-mesh"
    image_url: str | None = None
    lat: float | None = None         # None until GPS/Pixhawk is wired in
    lon: float | None = None
    route: str | None = None         # "DIRECT" | "RELAY", mesh alerts only
    hops: int | None = None
    rssi_dbm: float | None = None
    status: str = "new"              # new | acknowledged | resolved | false_alarm
    acknowledged_by: str | None = None
    acknowledged_at: float | None = None
    thermal_verified: bool | None = None  # camera-direct only; see DetectionWorker.verify_with_thermal
    # Simulated mission-map position -- deliberately separate fields from
    # lat/lon above, which keep meaning "a real GPS fix" (honestly None,
    # there is no GPS module on this rig). These are populated from
    # simulated_mission_position() for the map card only, and every place
    # they render must say SIMULATED -- see that function's docstring for
    # why this isn't allowed to share a field with a real fix.
    sim_lat: float | None = None
    sim_lon: float | None = None
    sim_x_m: float | None = None
    sim_y_m: float | None = None
    # Computed once at creation by correlating against recent hazard events
    # (see nearest_recent_hazard()) -- a static fact about what was nearby
    # when this alert was raised, not a live value that should keep
    # changing later. Feeds /api/triage's priority scoring and the
    # dashboard's "survivor near hazard" flag.
    near_hazard_type: str | None = None
    near_hazard_m: float | None = None


class AlertStore:
    """Alert history plus live fan-out to every connected SSE client.

    Persisted append-only to ALERTS_FILE so a Pi reboot mid-mission doesn't
    erase what a rescue team has already been told. Bounded in memory
    (MAX_ALERTS_IN_MEMORY) because a mission can run for hours and nothing
    here should grow without limit.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.alerts: list[Alert] = []
        self._subscribers: list[queue.Queue] = []
        self._load()

    def _load(self) -> None:
        if not ALERTS_FILE.exists():
            return
        try:
            with ALERTS_FILE.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.alerts.append(Alert(**json.loads(line)))
            self.alerts = self.alerts[-MAX_ALERTS_IN_MEMORY:]
            print(f"[*] Loaded {len(self.alerts)} alert(s) from {ALERTS_FILE}")
        except Exception as e:
            print(f"[!] Could not load {ALERTS_FILE}: {e}")

    def _append_to_disk(self, alert: Alert) -> None:
        try:
            with ALERTS_FILE.open("a") as f:
                f.write(json.dumps(asdict(alert)) + "\n")
        except Exception as e:
            print(f"[!] Could not persist alert: {e}")

    def create(self, **fields) -> Alert:
        alert = Alert(id=uuid.uuid4().hex[:12], created_at=time.time(), **fields)
        with self.lock:
            self.alerts.append(alert)
            self.alerts = self.alerts[-MAX_ALERTS_IN_MEMORY:]
            subscribers = list(self._subscribers)
        self._append_to_disk(alert)
        for q in subscribers:
            q.put(alert)
        print(f"[!] ALERT {alert.id}: {alert.source} conf={alert.confidence:.1f}% "
              f"persons={alert.person_count}")
        return alert

    def update_status(self, alert_id: str, status: str, by: str | None) -> Alert | None:
        with self.lock:
            for a in self.alerts:
                if a.id == alert_id:
                    a.status = status
                    a.acknowledged_by = by
                    a.acknowledged_at = time.time()
                    subscribers = list(self._subscribers)
                    updated = a
                    break
            else:
                return None
        for q in subscribers:
            q.put(updated)  # re-push so listeners see the status change too
        return updated

    def list_since(self, since_id: str | None = None) -> list[Alert]:
        with self.lock:
            if since_id is None:
                return list(self.alerts)
            for i, a in enumerate(self.alerts):
                if a.id == since_id:
                    return list(self.alerts[i + 1 :])
            return list(self.alerts)  # unknown id (e.g. rotated out) -> send all we have

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


alert_store = AlertStore()


# --------------------------------------------------------------------------
# Hazard events -- a deliberately separate store from Alert/AlertStore
# above. The SIH brief treats "survivor locations" and "hazard zones" as
# different marker types on the same map, and Alert's shape (person_count,
# thermal_verified, ...) is specifically about a human detection -- bolting
# hazard_type onto it would make every consumer guess which fields apply.
# Same persistence/SSE-fan-out pattern as AlertStore, on purpose: one
# pattern, two stores, not two different designs to learn.
# --------------------------------------------------------------------------

HAZARDS_FILE = Path("hazards.jsonl")
HAZARD_COOLDOWN_S = 15.0
"""Longer than ALERT_COOLDOWN_S (8s): a fire or smoke plume sits in frame
for minutes, not seconds, and the hazard model only runs every
HAZARD_CHECK_INTERVAL_S anyway (see HazardWorker) -- without this, the same
fire would re-raise a new event on every single hazard check."""

MAX_HAZARDS_IN_MEMORY = 500


@dataclass
class HazardEvent:
    """One hazard sighting -- fire or smoke today, whatever the model
    supports tomorrow. Position fields follow the same simulated-GPS
    honesty rule as Alert.sim_lat/sim_lon: see simulated_mission_position().
    """

    id: str
    created_at: float
    hazard_type: str          # "fire" | "smoke" -- whatever HAZARD_MODEL_PATH's model.names holds
    confidence: float
    image_url: str | None = None
    sim_lat: float | None = None
    sim_lon: float | None = None
    sim_x_m: float | None = None
    sim_y_m: float | None = None


class HazardStore:
    """Same shape as AlertStore: persisted history + live SSE fan-out."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[HazardEvent] = []
        self._subscribers: list[queue.Queue] = []
        self._load()

    def _load(self) -> None:
        if not HAZARDS_FILE.exists():
            return
        try:
            with HAZARDS_FILE.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.events.append(HazardEvent(**json.loads(line)))
            self.events = self.events[-MAX_HAZARDS_IN_MEMORY:]
            print(f"[*] Loaded {len(self.events)} hazard event(s) from {HAZARDS_FILE}")
        except Exception as e:
            print(f"[!] Could not load {HAZARDS_FILE}: {e}")

    def _append_to_disk(self, event: HazardEvent) -> None:
        try:
            with HAZARDS_FILE.open("a") as f:
                f.write(json.dumps(asdict(event)) + "\n")
        except Exception as e:
            print(f"[!] Could not persist hazard event: {e}")

    def create(self, **fields) -> HazardEvent:
        event = HazardEvent(id=uuid.uuid4().hex[:12], created_at=time.time(), **fields)
        with self.lock:
            self.events.append(event)
            self.events = self.events[-MAX_HAZARDS_IN_MEMORY:]
            subscribers = list(self._subscribers)
        self._append_to_disk(event)
        for q in subscribers:
            q.put(event)
        print(f"[!] HAZARD {event.id}: {event.hazard_type} conf={event.confidence:.1f}%")
        return event

    def list_since(self, since_id: str | None = None) -> list[HazardEvent]:
        with self.lock:
            if since_id is None:
                return list(self.events)
            for i, e in enumerate(self.events):
                if e.id == since_id:
                    return list(self.events[i + 1 :])
            return list(self.events)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


hazard_store = HazardStore()


@dataclass
class MeshStatus:
    """Live link health for the LoRa ground-station radio, kept separate from
    AlertStore because most of what a ground-station operator wants to see
    -- is the radio even hearing anything right now -- happens on frames
    that never become an alert (telemetry, or simply no detection to
    report). lora_bridge.py posts one of these on every decoded frame, not
    just HUMAN_DETECTED ones, so the dashboard can show a live channel
    rather than going quiet between detections.
    """

    connected: bool = False
    esp32_port: str | None = None
    last_heartbeat_at: float | None = None
    last_rssi_dbm: int | None = None
    last_snr_db: float | None = None
    total_frames: int = 0
    total_detections: int = 0
    first_heartbeat_at: float | None = None
    # Frames heard with hop > 0 (i.e. actually retransmitted by NODE-01),
    # separate from total_frames/total_detections above -- those count
    # whatever AlertStore's dedup ends up storing, normally whichever copy
    # (direct or relayed) arrived first. See lora_bridge.py's heartbeat()
    # docstring for why that alone would hide real, working relay activity.
    relay_total_frames: int = 0
    relay_last_seen_at: float | None = None


SKIN_YCRCB_LOW = np.array((0, 133, 77), dtype=np.uint8)
SKIN_YCRCB_HIGH = np.array((255, 173, 127), dtype=np.uint8)
"""Classical skin-tone range in YCrCb (Chai & Ngan 1999's commonly-cited
bounds) -- Cr/Cb encode colour independent of brightness, which is why
skin detection uses this space rather than raw RGB thresholds."""

VERIFIED_BOX_COLOR = (0, 210, 90)     # BGR green -- RGB detector + thermal heuristic agree
UNVERIFIED_BOX_COLOR = (0, 200, 255)  # BGR amber -- RGB detector only, thermal check didn't confirm

THERMAL_VERIFY_SKIN_RATIO = 0.025
"""Fraction of skin-tone pixels a detected box needs to count as
"thermally verified" (see DetectionWorker.verify_with_thermal).
Deliberately low: this is a real but weak, lighting- and skin-tone-
dependent classical signal, not a trained model, and this project already
prioritises not missing a real survivor over rejecting borderline
detections (see the conf=0.25 reasoning below) -- a second-stage filter
must carry the same bias or it quietly reintroduces the recall loss the
first stage was tuned to avoid.

Calibrated 2026-09-23 against one real person on this Pi's camera/
lighting: skin_ratio measured 0.0-0.006 when not clearly facing the
camera (back turned, out of frame) and 0.043-0.064 when facing it --
0.025 sits with margin below the "facing" cluster and above the "not
really visible" one. This is one person, one room, one camera -- it WILL
need re-tuning against real footage across skin tones and lighting before
this can be trusted for anything beyond a demo."""

mesh_status = MeshStatus()
MESH_STALE_AFTER_S = 20.0
"""If lora_bridge.py hasn't posted in this long, treat the link as down
rather than showing a stale RSSI as if it were current -- the bridge posts
on every decoded frame, so a real silence this long means the process
exited, the ESP32 disconnected, or the channel genuinely stopped, not that
a normal gap is being over-interpreted."""


# --------------------------------------------------------------------------
# Simulated mission position -- there is no GPS module on this rig (every
# lat/lon on Alert above is None for exactly that reason). This generates a
# plausible, continuously-moving position so the Command Center's map has
# something to show, using the same honesty discipline as the thermal view
# (see DetectionWorker.make_thermal_view): it is fully synthetic, it is
# labelled SIMULATED everywhere it surfaces, and it never writes into the
# real lat/lon fields that the rest of the system already correctly treats
# as "a real GPS fix" -- a fabricated survivor position is a much worse
# failure than a fabricated thermal image, since a rescue team could act on
# it directly. Deliberately a pure function of wall-clock time (not stored
# state): every caller -- an alert raised this instant, the map polling a
# moment later -- computes the same position independently, so nothing can
# drift out of sync or needs a background thread.
# --------------------------------------------------------------------------

MISSION_ORIGIN_LAT = 11.0374   # centered on where this Pi actually sits during
MISSION_ORIGIN_LON = 76.9840   # bench testing (was checkradio.py's demo packet
                               # origin, ~2-3km away -- any real WiFi-geolocation
                               # fix clamped to the simulated box's edge there,
                               # which made the drone marker, and now the
                               # survivor/responder path (see api_team_nearest),
                               # collapse to a single corner point instead of a
                               # meaningful position. The simulated lawnmower
                               # pattern is unaffected: it's pure sim_x_m/sim_y_m,
                               # never converted through this origin at all.
MISSION_AREA_M = 400.0         # simulated search box: MISSION_AREA_M square
LAWNMOWER_SPEED_MPS = 4.0      # a slow, plausible search groundspeed
LAWNMOWER_LANE_SPACING_M = 40.0
METERS_PER_DEGREE_LAT = 111_320.0


def _meters_to_latlon(dx_m: float, dy_m: float) -> tuple[float, float]:
    lat = MISSION_ORIGIN_LAT + dy_m / METERS_PER_DEGREE_LAT
    lon = MISSION_ORIGIN_LON + dx_m / (METERS_PER_DEGREE_LAT * math.cos(math.radians(MISSION_ORIGIN_LAT)))
    return lat, lon


def _latlon_to_meters(lat: float, lon: float) -> tuple[float, float]:
    """Inverse of _meters_to_latlon -- for a REAL fix (see LocationWorker
    below), converts back to the same local x_m/y_m frame the map's SVG
    already places every marker in, so a real position slots into the
    existing tactical map without it needing its own coordinate system.
    Same flat-earth approximation as the forward function; fine at this
    (city-block) scale, not meant for anything larger."""
    dy_m = (lat - MISSION_ORIGIN_LAT) * METERS_PER_DEGREE_LAT
    dx_m = (lon - MISSION_ORIGIN_LON) * METERS_PER_DEGREE_LAT * math.cos(math.radians(MISSION_ORIGIN_LAT))
    return dx_m, dy_m


def simulated_mission_position(t: float | None = None) -> dict:
    """A lawnmower/boustrophedon search pattern over a MISSION_AREA_M
    square -- the standard coverage pattern a real search drone flies, so
    the map looks like a plausible mission rather than a random wander.
    NOT a real position; see the module comment above.
    """
    t = time.time() if t is None else t
    lane_count = max(1, int(MISSION_AREA_M / LAWNMOWER_LANE_SPACING_M))
    pattern_length_m = lane_count * MISSION_AREA_M
    pos_in_pattern = (LAWNMOWER_SPEED_MPS * t) % pattern_length_m
    lane = int(pos_in_pattern // MISSION_AREA_M)
    pos_in_lane = pos_in_pattern % MISSION_AREA_M
    x = pos_in_lane if lane % 2 == 0 else (MISSION_AREA_M - pos_in_lane)
    y = lane * LAWNMOWER_LANE_SPACING_M
    lat, lon = _meters_to_latlon(x, y)
    return {"lat": lat, "lon": lon, "x_m": round(x, 1), "y_m": round(y, 1)}


def simulated_coverage_pct(t: float | None = None) -> float:
    """Progress through the CURRENT lawnmower sweep, as a pure function of
    elapsed time -- same determinism as simulated_mission_position() and
    for the same reason (every caller computes the same answer
    independently, nothing to keep in sync).

    Uses the same `% pattern_length_m` wrap simulated_mission_position()
    does, not raw `t` -- `t` is wall-clock time.time() (a multi-billion-
    second epoch value), and computing progress against that directly
    would saturate at 100% permanently within the first second of any
    process start, never showing real progress. Cycling 0-100% once per
    full pass (~1000s at the default speed/area) is what actually reads as
    "still searching" vs "just finished a pass" on a live dashboard.
    """
    t = time.time() if t is None else t
    lane_count = max(1, int(MISSION_AREA_M / LAWNMOWER_LANE_SPACING_M))
    pattern_length_m = lane_count * MISSION_AREA_M
    pos_in_pattern = (LAWNMOWER_SPEED_MPS * t) % pattern_length_m
    return round((pos_in_pattern / pattern_length_m) * 100.0, 1)


HAZARD_CORRELATION_RADIUS_M = 60.0
HAZARD_CORRELATION_WINDOW_S = 300.0
"""A survivor detection counts as "near" a hazard if within this radius AND
the hazard was seen within this many seconds -- an old, long-gone fire
event shouldn't keep flagging new detections as critical forever."""


def nearest_recent_hazard(x_m: float, y_m: float) -> tuple[str, float] | None:
    """Correlates one detection's simulated position against recent hazard
    events. Real, if modest, reasoning: this is what lets the dashboard say
    "survivor 18m from an active fire" instead of two unrelated marker
    lists sitting on the same map. Returns (hazard_type, distance_m) for
    the closest qualifying hazard, or None.
    """
    now = time.time()
    best: tuple[str, float] | None = None
    for event in hazard_store.list_since():
        if event.sim_x_m is None or now - event.created_at > HAZARD_CORRELATION_WINDOW_S:
            continue
        dist = math.hypot(event.sim_x_m - x_m, event.sim_y_m - y_m)
        if dist <= HAZARD_CORRELATION_RADIUS_M and (best is None or dist < best[1]):
            best = (event.hazard_type, round(dist, 1))
    return best


def triage_priority(alert) -> dict:
    """Explainable priority score for one alert -- deliberately a visible
    sum of named terms, not a trained/opaque model, because a rescue
    coordinator needs to see WHY something is ranked first, not just a
    number to trust blindly.
    """
    reasons = []
    score = alert.confidence
    reasons.append(f"{alert.confidence:.0f}% detection confidence")

    if alert.thermal_verified:
        score += 20
        reasons.append("thermal-verified")

    if alert.near_hazard_type:
        score += 40
        reasons.append(f"{alert.near_hazard_m:.0f}m from active {alert.near_hazard_type}")

    age_min = (time.time() - alert.created_at) / 60.0
    decay = min(30.0, age_min * 2.0)
    score -= decay
    if age_min > 1:
        reasons.append(f"{age_min:.0f} min since detection")

    return {"score": round(score, 1), "reasons": reasons}


def generate_mission_report() -> str:
    """Plain-English mission briefing synthesised from live state -- the
    SIH brief's own "generate situational reports" line, taken literally.
    Every number here is read from real state (AlertStore, HazardStore,
    the mesh/camera status), not invented for the summary -- if a figure
    looks wrong, the bug is upstream of this function, not in it.
    """
    alerts = alert_store.list_since()
    hazards = hazard_store.list_since()
    verified = sum(1 for a in alerts if a.thermal_verified)
    active_hazards = [h for h in hazards if time.time() - h.created_at < HAZARD_CORRELATION_WINDOW_S]
    coverage = simulated_coverage_pct()
    critical = [a for a in alerts if a.near_hazard_type]

    lines = [
        f"MISSION REPORT -- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Search coverage: {coverage:.0f}% of the designated area (simulated position).",
        f"Survivors detected: {len(alerts)} total, {verified} thermally verified.",
    ]
    if critical:
        lines.append(f"CRITICAL: {len(critical)} survivor detection(s) near an active hazard -- see priority queue.")
    if active_hazards:
        types = ", ".join(sorted({h.hazard_type for h in active_hazards}))
        lines.append(f"Active hazards in the last {HAZARD_CORRELATION_WINDOW_S/60:.0f} min: {len(active_hazards)} ({types}).")
    else:
        lines.append("No active hazards in the recent window.")
    lines.append(
        "Recommendation: " + (
            "dispatch to nearest critical marker immediately."
            if critical else
            "continue search pattern; no survivors currently flagged as high-risk."
        )
    )
    return "\n".join(lines)


class DetectionWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame_ready = threading.Condition(self.lock)
        self.frame_sequence = 0
        self.thermal_sequence = 0
        self.thermal_clients = 0
        self.running = True

        # Configuration defaults. imgsz is never set independently of the
        # model -- see MODEL_REGISTRY and load_model().
        self.model_name = DEFAULT_MODEL
        self.imgsz = MODEL_REGISTRY[DEFAULT_MODEL]["imgsz"]
        # Moved down from 0.35: the checkpoint's own validation run scores
        # 82.3% precision but only 65.2% recall -- there is real headroom to
        # trade a bit of precision for recall before false positives become
        # a problem. This is a reasoned starting point, not a value derived
        # from a measured PR curve on this exact deployment; re-tune it once
        # real test footage in adequate light is available (see the exposure
        # note in init_camera -- at ~8 lux nothing detects at any threshold).
        self.conf = 0.25
        self.model = None

        # Camera & State
        self.picam2 = None
        self.net_cap = None  # cv2.VideoCapture for a network (e.g. phone) stream -- see CAMERA_STREAM_URL
        self.camera_source = "none"  # "pi_camera_module" | "phone" | "usb_webcam" | "none"
        self.camera_active = False
        self.latest_jpeg = None
        self.latest_frame_bgr = None
        self.latest_thermal_jpeg = None
        self.latest_raw_frame_bgr = None  # freshest capture, no overlay -- written by _capture_loop at the camera's own rate, decoupled from inference cadence
        self.stream_fps = 0.0             # _capture_loop's own throughput -- what the viewer's video actually runs at; see `fps` below for inference throughput, a different (usually much lower) number
        self._latest_detections = []      # most recent inference pass's boxes, for _capture_loop to composite onto each new raw frame without re-running the model
        self._latest_hud_text = ""

        # Recording
        self.is_recording = False
        self.record_writer = None
        self.record_frames_remaining = 0

        # Stats
        self.fps = 0.0
        self.latency_ms = 0.0
        self.persons_detected = 0
        self.persons_verified = 0
        self.total_frames = 0
        self.start_time = time.time()
        self._last_alert_at = 0.0

        self.load_model(self.model_name)

    def load_model(self, model_path):
        """Load or switch the active YOLO model.

        Sets imgsz from MODEL_REGISTRY, not from whatever the caller had
        lying around -- this is the one place that decides it, so it can
        never drift out of sync with the model actually loaded.
        """
        entry = MODEL_REGISTRY.get(model_path)
        if entry is None:
            print(f"[!] Unknown model '{model_path}', ignoring. Known: {list(MODEL_REGISTRY)}")
            return
        with self.lock:
            try:
                print(f"[*] Dashboard loading model: {model_path} ({entry['label']})")
                self.model = YOLO(model_path, task="detect")
                self._runtime_tuned = False
                self.inference_threads = None
                self.model_name = model_path
                self.imgsz = entry["imgsz"]
                print(f"[+] Model '{model_path}' loaded -- locked to imgsz={self.imgsz}.")
            except Exception as e:
                print(f"[!] Error loading model '{model_path}': {e}")

    def init_camera(self):
        """Pick a camera source, highest priority first:
        1. CAMERA_STREAM_URL (phone over WiFi/USB-tunnel) -- explicit config
           always wins when set.
        2. USB_CAMERA_DEVICE (a UVC webcam plugged into the Pi) -- tried
           whenever the device node exists, since plugging one in is itself
           a deliberate choice to use it over the ribbon-cable module.
        3. The Pi's own camera module -- fallback.
        See the module-level comments on CAMERA_STREAM_URL / USB_CAMERA_DEVICE
        for why nothing downstream needs to know which one is actually active.
        """
        if CAMERA_STREAM_URL:
            return self._init_network_camera()
        if os.path.exists(USB_CAMERA_DEVICE):
            if self._init_usb_camera():
                return True
            print("[!] USB webcam present but failed to open -- falling back to Pi camera module.")
        return self._init_picamera()

    def _init_network_camera(self):
        """Phone-as-camera over WiFi (e.g. the 'IP Webcam' Android app's
        MJPEG stream) instead of the Pi's own attached camera module.

        Deliberately assumes MJPEG, not RTSP/H.264: a real video codec's
        GOP/B-frame lookahead and an RTSP jitter buffer each add anywhere
        from a few hundred ms to a couple of seconds of latency that
        motion-JPEG -- one independently-compressed frame at a time, no
        inter-frame dependencies -- doesn't have. That gap is the whole
        reason to pick MJPEG here: this feed needs to be live, not smooth.

        CAP_PROP_BUFFERSIZE=1 matters for the same reason: OpenCV's FFmpeg
        backend will happily queue several frames if this loop's
        capture+inference cycle is ever slower than the source's frame
        rate, and every queued frame is extra latency the pipeline can
        never claw back -- it just watches the past. Capping the buffer at
        1 forces every read() to return the newest frame, dropping stale
        ones instead of accumulating them.
        """
        try:
            print(f"[*] Connecting to network camera: {CAMERA_STREAM_URL}")
            self.net_cap = cv2.VideoCapture(CAMERA_STREAM_URL)
            self.net_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not self.net_cap.isOpened():
                raise RuntimeError("stream did not open")
            ok, _ = self.net_cap.read()
            if not ok:
                raise RuntimeError("stream opened but returned no frame")
            self.camera_active = True
            self.camera_source = "phone"
            print("[+] Network camera (phone) active and streaming.")
            return True
        except Exception as e:
            print(f"[!] Failed to connect to network camera ({CAMERA_STREAM_URL}): {e}")
            self.camera_active = False
            self.camera_source = "none"
            if self.net_cap is not None:
                self.net_cap.release()
            self.net_cap = None
            return False

    def _init_usb_camera(self):
        """USB UVC webcam plugged directly into the Pi (USB_CAMERA_DEVICE,
        default /dev/video0). Opened via the V4L2 backend explicitly --
        OpenCV's auto-selected backend has been observed to fall back to a
        slow generic path on some builds -- and forced to MJPG: this
        particular cam's YUYV mode is bandwidth-capped over USB2 to far
        lower resolution/fps than its MJPG mode (see `v4l2-ctl -d
        /dev/video0 --list-formats-ext`).

        Captured at 1024x576 -- a middle ground the format table also
        lists under MJPG@30fps. Earlier this ran at 640x480 as a
        thermal-throttling mitigation (SoC was hitting `vcgencmd
        get_throttled` -> 0xe0006, ARM clock held at 1.5GHz instead of its
        2.4GHz max -- see `vcgencmd measure_clock arm` / `measure_temp`);
        with an Active Cooler now attached and confirmed keeping the SoC
        un-throttled under full load, that specific problem is gone. But
        _capture_loop's own per-frame compositing+JPEG-encode cost scales
        with pixel count and runs concurrently with inference (see
        STREAM_TARGET_FPS's comment) -- at the full 1280x720 this cam also
        supports, that cost alone pushed all 4 cores to 100% and dragged
        inference down to ~1.7fps from ~9fps. 1024x576 (64% of 720p's
        pixels) was chosen empirically to keep the stream meaningfully
        sharper than the old 640x480 without re-starving inference; raise
        it back to 1280x720 if a future change frees up more CPU headroom
        and it's worth re-checking inference fps against. This resolution
        only reaches the human viewer: the detector still resizes every
        frame down to imgsz (416) regardless of source size, and
        VisualOdometryWorker resizes back
        down to its own fixed vo_frame_size before feature detection (see
        that class -- its intrinsics are calibrated for a specific size and
        would silently desync if fed this resolution directly), so neither
        one pays for or benefits from the extra detail here. Same
        CAP_PROP_BUFFERSIZE=1 low-latency reasoning as _init_network_camera.
        Reuses self.net_cap -- the capture loop only branches on
        `self.camera_active and self.net_cap`, not on camera_source, so no
        other code needs to change for a third source.
        """
        try:
            print(f"[*] Initializing USB webcam ({USB_CAMERA_DEVICE})...")
            self.net_cap = cv2.VideoCapture(USB_CAMERA_DEVICE, cv2.CAP_V4L2)
            self.net_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.net_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1024)
            self.net_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 576)
            self.net_cap.set(cv2.CAP_PROP_FPS, STREAM_TARGET_FPS)
            self.net_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not self.net_cap.isOpened():
                raise RuntimeError("device did not open")
            ok, _ = self.net_cap.read()
            if not ok:
                raise RuntimeError("device opened but returned no frame")
            self.camera_active = True
            self.camera_source = "usb_webcam"
            print("[+] USB webcam active and streaming.")
            return True
        except Exception as e:
            print(f"[!] Failed to initialize USB webcam ({USB_CAMERA_DEVICE}): {e}")
            self.camera_active = False
            self.camera_source = "none"
            if self.net_cap is not None:
                self.net_cap.release()
            self.net_cap = None
            return False

    def _init_picamera(self):
        """Initialize Picamera2 hardware interface.

        FrameDurationLimits is set explicitly because the previous default
        configuration left it unset, which capped every frame at ~33ms
        regardless of how dark the scene was -- auto-exposure was never
        actually allowed to use the sensor's full ~66ms capability. Measured
        on this Pi in a dim room (~8 lux): default config pegged at 33167us
        exposure / 42.0 mean brightness; with the wider limit below, AE
        reached 66638us / 56.5 mean brightness, a real (if partial) recovery.
        This does NOT fix genuinely dark scenes -- at ~8 lux, brightness
        stayed too low for any model here to detect a person at any
        confidence threshold down to 0.01. There is no substitute for actual
        light reaching the sensor; this just stops throwing away exposure
        capacity the hardware already has.
        """
        if not HAS_PICAM2:
            print("[!] Picamera2 not available on system.")
            return False

        try:
            print("[*] Initializing Pi Camera via Picamera2...")
            self.picam2 = Picamera2()
            config = self.picam2.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"},
                controls={"FrameDurationLimits": (33333, 100000)},  # allow AE down to 10fps-equivalent exposure
            )
            self.picam2.configure(config)
            self.picam2.start()
            time.sleep(1.0)  # let AE/AWB converge to the widened limit before the first real frame
            self.camera_active = True
            self.camera_source = "pi_camera_module"
            print("[+] Pi Camera active and streaming.")
            return True
        except Exception as e:
            print(f"[!] Failed to initialize Pi Camera: {e}")
            self.camera_active = False
            self.camera_source = "none"
            return False

    def generate_dummy_frame(self):
        """Synthetic frame if camera is disconnected."""
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[:] = (20, 20, 30)
        cv2.putText(
            img,
            "CAMERA INITIALIZING / OFFLINE",
            (80, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 150, 255),
            2,
        )
        return img

    def make_thermal_view(self, frame_bgr, detections=None):
        """Simulated thermal view from the RGB sensor -- there is no IR
        camera on this rig. Grayscale + a contrast boost + the classic
        blue-cyan-green-yellow-red "ironbow"/JET colormap consumer thermal
        cameras use (background reads cold/blue, warmer or more-reflective
        surfaces read toward red/white). This is a visual match for what a
        real thermal camera looks like -- it is NOT a real temperature
        measurement, nothing here senses heat, only visible light.

        The small corner tag is deliberately kept, deliberately small: on
        a disaster-response tool specifically, showing this as real
        heat-imaging with no indication otherwise risks a real person
        trusting a capability (seeing through smoke, darkness, foliage)
        this sensor cannot actually provide. It stays out of the way of
        the shot; it doesn't stay out of existing.

        `detections` (from compute_detections) carries each box's
        thermal_verified flag so this view draws the *same*
        verification-coloured boxes as the RGB view -- the two feeds
        should visibly agree, not show independently-drawn boxes.
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        # Bias each detected person's region toward the hot end before
        # colormapping. Pure brightness has no reason to put a *person* at
        # the warm end -- a bright wall or window often reflects more
        # visible light than skin or hair does, which read backwards
        # (person cool, background warm) against real thermal footage.
        # Blending toward a warm baseline inside the AI-found region, not
        # the whole frame, is what makes a found person consistently read
        # hot the way a real thermal camera would show a body against a
        # cooler background -- it rides on the detector's own box, not a
        # separate heat estimate.
        for d in (detections or []):
            x1, y1, x2, y2 = d["bbox"]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(gray.shape[1], x2), min(gray.shape[0], y2)
            if x2 <= x1 or y2 <= y1:
                continue
            region = gray[y1:y2, x1:x2].astype(np.float32)
            gray[y1:y2, x1:x2] = np.clip(region * 0.4 + 235 * 0.6, 0, 255).astype(np.uint8)

        thermal = cv2.applyColorMap(gray, cv2.COLORMAP_JET)
        for d in (detections or []):
            x1, y1, x2, y2 = d["bbox"]
            color = VERIFIED_BOX_COLOR if d["thermally_verified"] else UNVERIFIED_BOX_COLOR
            cv2.rectangle(thermal, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            thermal, "IR", (thermal.shape[1] - 30, thermal.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA,
        )
        return thermal

    def verify_with_thermal(self, frame_bgr, box_xyxy):
        """A second, independently-computed signal for one detected box --
        not a re-run of the person model, and not derived from the
        colour-mapped thermal display (that is this same data repainted,
        not new information). Crops the box from the raw frame and checks
        what fraction falls in the classical skin-tone range in YCrCb
        space (SKIN_YCRCB_LOW/HIGH), which responds to different pixel
        statistics than the CNN's learned features do.

        This is NOT real thermal/IR sensing -- see make_thermal_view's
        docstring -- it is a second visible-light heuristic standing in
        for the cross-check a real second sensor would provide, and it
        inherits every weakness classical skin detection has: lighting-
        dependent, uneven across skin tones, fooled by skin-toned fabric
        or wood. Treat it as a weak corroborating signal, never a
        replacement for the primary detector -- see THERMAL_VERIFY_SKIN_
        RATIO's own reasoning on why the bar is set low.

        Returns (verified: bool, skin_ratio: float) so callers can show
        their work instead of a bare true/false.
        """
        x1, y1, x2, y2 = box_xyxy
        h, w = frame_bgr.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return False, 0.0
        crop = frame_bgr[y1:y2, x1:x2]
        ycrcb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, SKIN_YCRCB_LOW, SKIN_YCRCB_HIGH)
        skin_ratio = float(np.count_nonzero(mask)) / mask.size
        return skin_ratio >= THERMAL_VERIFY_SKIN_RATIO, skin_ratio

    def compute_detections(self, frame_bgr, boxes):
        """Runs verify_with_thermal per box and returns detection dicts --
        no drawing. Split out of the old draw_detections so the capture
        loop can composite the same boxes onto fresh frames (see
        draw_overlay) without re-running thermal verification, and without
        the streaming path ever touching the model.
        """
        detections = []
        if boxes is None or len(boxes) == 0:
            return detections

        xyxy = boxes.xyxy.cpu().numpy().astype(int)
        confs = boxes.conf.cpu().numpy()
        for (x1, y1, x2, y2), conf in zip(xyxy, confs):
            verified, skin_ratio = self.verify_with_thermal(frame_bgr, (x1, y1, x2, y2))
            detections.append({
                "bbox": (int(x1), int(y1), int(x2), int(y2)),
                "confidence": float(conf) * 100.0,
                "thermally_verified": verified,
                "skin_ratio": round(skin_ratio, 3),
            })
        return detections

    def draw_overlay(self, frame_bgr, detections, hud_text=""):
        """Composite already-computed detection boxes (colour-coded by
        thermal verification, replacing Ultralytics' own res.plot(), which
        has no way to vary colour per box on a signal it doesn't know
        about) plus an optional HUD line onto a frame. Called once per
        inference pass (for the alert photo, recording, and
        latest_frame_bgr) and, separately and far more often, by
        _capture_loop on every fresh raw frame -- so the live stream can
        run at full capture rate while still showing the most recent
        detection result, instead of waiting on the next inference pass.
        """
        drawn = frame_bgr.copy()
        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            verified = d["thermally_verified"]
            color = VERIFIED_BOX_COLOR if verified else UNVERIFIED_BOX_COLOR
            cv2.rectangle(drawn, (x1, y1), (x2, y2), color, 2)

            label = f"person {d['confidence'] / 100:.2f} - {'thermal OK' if verified else 'unconfirmed'}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            label_y = max(th + 6, y1)
            cv2.rectangle(drawn, (x1, label_y - th - 6), (x1 + tw + 6, label_y), color, -1)
            cv2.putText(drawn, label, (x1 + 3, label_y - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        if hud_text:
            cv2.putText(drawn, hud_text, (14, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 170), 2, cv2.LINE_AA)
        return drawn

    def _capture_loop(self):
        """Continuously pulls frames from whichever camera source is
        active and republishes them -- with the most recent detection
        overlay composited on -- at the camera's own rate, completely
        decoupled from run()'s YOLO inference cadence.

        Before this split, capture and inference lived in one loop, so
        /video_feed's frame rate WAS the inference rate (~100-500ms/frame
        on this Pi): raising capture resolution for a sharper picture
        directly fought against a smooth stream, and there was no way to
        have both. Now the viewer gets full-rate video with boxes that
        refresh as fast as inference keeps up, instead of the video itself
        waiting on model.predict(). thermal_frame generation stays in
        run() (inference cadence is plenty for a labelled-simulated
        secondary view) -- only the primary stream and stream_fps live
        here.
        """
        fps_ema = 0.0
        while self.running:
            t0 = time.perf_counter()
            frame_bgr = None
            if self.camera_active and self.picam2:
                try:
                    frame_rgb = self.picam2.capture_array()
                    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                except Exception as e:
                    print(f"[!] Camera capture error: {e}")
                    time.sleep(0.1)
                    continue
            elif self.camera_active and self.net_cap:
                try:
                    ok, frame_bgr = self.net_cap.read()
                    if not ok:
                        raise RuntimeError("read() returned no frame")
                except Exception as e:
                    print(f"[!] Network camera read error: {e}")
                    time.sleep(0.1)
                    continue
            else:
                frame_bgr = self.generate_dummy_frame()
                time.sleep(0.1)

            with self.lock:
                detections = self._latest_detections
                hud_text = self._latest_hud_text
            annotated = self.draw_overlay(frame_bgr, detections, hud_text)
            _, jpeg_data = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 78])

            with self.lock:
                self.latest_raw_frame_bgr = frame_bgr
                self.latest_jpeg = jpeg_data.tobytes()
                self.frame_sequence += 1
                self.frame_ready.notify_all()

            # Pace to STREAM_TARGET_FPS -- see its module comment. Sleeping
            # off any headroom (rather than looping flat-out) is what
            # actually leaves inference a predictable share of the CPU;
            # instant_fps/fps_ema below measure the PACED rate, i.e. what
            # the viewer really gets, not the camera's raw capability.
            min_frame_time = 1.0 / STREAM_TARGET_FPS
            frame_time = time.perf_counter() - t0
            if frame_time < min_frame_time:
                time.sleep(min_frame_time - frame_time)
                frame_time = min_frame_time
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0.0
            fps_ema = instant_fps if fps_ema == 0.0 else (0.85 * fps_ema + 0.15 * instant_fps)
            with self.lock:
                self.stream_fps = fps_ema

    def run(self):
        """Main inference loop. Camera I/O now lives in _capture_loop
        (started below), running independently at the camera's own rate --
        this loop only ever reads whatever self.latest_raw_frame_bgr holds
        most recently, so a slow inference pass never holds up capture or
        the live stream. See _capture_loop's docstring for why."""
        if not self.init_camera():
            print("[*] Running in synthetic camera fallback mode.")
        threading.Thread(target=self._capture_loop, daemon=True).start()

        fps_ema = 0.0
        last_sequence = -1

        while self.running:
            with self.frame_ready:
                self.frame_ready.wait_for(lambda: self.frame_sequence != last_sequence or not self.running, timeout=1)
                if self.frame_sequence == last_sequence:
                    continue
                last_sequence = self.frame_sequence
                frame_bgr = self.latest_raw_frame_bgr
                want_thermal = self.thermal_clients > 0
            if frame_bgr is None:
                time.sleep(0.02)  # _capture_loop hasn't produced a first frame yet
                continue

            t_infer_start = time.perf_counter()

            # 1. Run Inference
            detections = []
            num_persons = 0
            num_verified = 0
            top_confidence = 0.0
            top_verified = False
            latency = 0.0

            if self.model is not None:
                try:
                    results = self.model.predict(
                        source=frame_bgr,
                        conf=self.conf,
                        imgsz=self.imgsz,
                        device="cpu",
                        verbose=False,
                    )
                    res = results[0]
                    if not self._runtime_tuned:
                        # NCNN ignores OMP_NUM_THREADS and defaults to all four Pi cores.
                        # Reserve headroom for camera encoding, hazard detection and HTTP.
                        backend = self.model.predictor.model
                        runtime = getattr(backend, "backend", backend)
                        net = getattr(runtime, "net", None)
                        if net is not None and hasattr(net, "opt"):
                            # GEMM records thread count at weight-load time. Reload once
                            # before publishing normal inference results with this budget.
                            weight_path = Path(self.model_name)
                            param_path = weight_path if weight_path.is_file() else next(weight_path.glob("*.param"))
                            net.clear()
                            net.opt.num_threads = 2
                            if hasattr(net.opt, "openmp_blocktime"):
                                net.opt.openmp_blocktime = 0
                            if net.load_param(str(param_path)) != 0 or net.load_model(str(param_path.with_suffix(".bin"))) != 0:
                                raise RuntimeError("Could not reload NCNN model with CPU budget")
                            self.inference_threads = net.opt.num_threads
                            print(f"[*] NCNN CPU budget: {self.inference_threads} threads, no spin wait")
                        self._runtime_tuned = True
                    detections = self.compute_detections(frame_bgr, res.boxes)
                    num_persons = len(detections)
                    num_verified = sum(1 for d in detections if d["thermally_verified"])
                    if detections:
                        best = max(detections, key=lambda d: d["confidence"])
                        top_confidence = best["confidence"]
                        top_verified = best["thermally_verified"]
                    latency = (time.perf_counter() - t_infer_start) * 1000
                except Exception as e:
                    print(f"[!] Inference error: {e}")

            annotated = self.draw_overlay(frame_bgr, detections)  # boxes only, no HUD yet
            thermal_frame = self.make_thermal_view(frame_bgr, detections) if want_thermal else None

            # 1b. Emergency alert -- cooldown-gated so a person standing in
            # frame produces one alert every ALERT_COOLDOWN_S, not one per
            # inference pass. Runs before the HUD text is drawn on
            # `annotated` so the saved alert image is a clean detection
            # photo.
            #
            # Not gated on thermal_verified: the skin-tone heuristic is a
            # weak, untested second-stage signal (see
            # THERMAL_VERIFY_SKIN_RATIO), and blocking a real alert on it
            # would risk the one failure mode this project cannot accept --
            # missing an actual survivor. thermal_verified rides along on
            # the alert as an extra confidence signal for the rescue team
            # to see, not a gate on whether they see it at all.
            if num_persons > 0 and (time.time() - self._last_alert_at) >= ALERT_COOLDOWN_S:
                self._last_alert_at = time.time()
                self._raise_alert(annotated, top_confidence, num_persons, top_verified)

            frame_time = time.perf_counter() - t_infer_start
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0.0
            fps_ema = instant_fps if fps_ema == 0.0 else (0.85 * fps_ema + 0.15 * instant_fps)

            # 2. Telemetry Overlay -- mutated in place onto the already-
            # boxed `annotated`, after the alert photo above was saved, so
            # the alert image never carries this line.
            # "Detect" (inference passes/sec), not "FPS" -- since the
            # capture/inference split, the video itself runs at
            # stream_fps (see _capture_loop), a different and usually
            # much higher number. Labelling this plain "FPS" would read
            # as a claim about the smooth video the viewer is actually
            # watching, when it's really how often a NEW detection result
            # lands.
            hud_text = f"Detect: {fps_ema:.1f}/s | {latency:.0f}ms | Targets: {num_persons} ({num_verified} verified)"
            cv2.putText(
                annotated,
                hud_text,
                # y=52, not 26: the dashboard's own RGB/THERMAL toggle
                # overlay sits at the top-left corner (top:12px;left:12px
                # in ml_dashboard.html) -- this leaves it clear instead of
                # running text underneath it.
                (14, 52),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 170),
                2,
                cv2.LINE_AA,
            )

            # 3. Handle Video Recording
            if self.is_recording and self.record_writer:
                self.record_writer.write(annotated)
                self.record_frames_remaining -= 1
                if self.record_frames_remaining <= 0:
                    self.is_recording = False
                    self.record_writer.release()
                    self.record_writer = None
                    print("[+] Video recording completed.")

            # 4. Thermal JPEG encode -- the main stream's own JPEG is now
            # encoded by _capture_loop instead, at the camera's rate.
            thermal_jpeg_data = None
            if thermal_frame is not None:
                _, thermal_jpeg_data = cv2.imencode(
                    ".jpg", thermal_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75]
                )

            # 5. Update thread-safe state. _latest_detections/_latest_hud_text
            # feed _capture_loop's overlay; everything else is unchanged
            # from before the capture/inference split.
            with self.lock:
                self.latest_frame_bgr = annotated
                if thermal_jpeg_data is not None:
                    self.latest_thermal_jpeg = thermal_jpeg_data.tobytes()
                    self.thermal_sequence += 1
                    self.frame_ready.notify_all()
                self._latest_detections = detections
                self._latest_hud_text = hud_text
                self.fps = fps_ema
                self.latency_ms = latency
                self.persons_detected = num_persons
                self.persons_verified = num_verified
                self.total_frames += 1

    def _raise_alert(
        self, annotated_frame, confidence: float, person_count: int, thermal_verified: bool
    ) -> None:
        """Save the detection photo and create an alert the mobile app will see.

        `lat`/`lon` are left None here deliberately -- this Pi has no GPS or
        Pixhawk link wired in yet (see MODEL_REGISTRY comment block above).
        Reporting a fabricated position would be worse than reporting none;
        an app screen that says "no GPS lock" is honest, a pin on a map that
        is quietly wrong is not. Wire real coordinates in here once GPS/
        MAVLink is available.

        `thermal_verified` is the skin-tone heuristic's opinion on the
        highest-confidence box in this frame (see
        DetectionWorker.verify_with_thermal) -- carried along as extra
        context for the rescue team, never used to suppress the alert
        itself. See the caller's own comment on why it isn't a gate.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"alert_{ts}.jpg"
        try:
            cv2.imwrite(str(RUNS_DIR / filename), annotated_frame)
            image_url = f"/runs/detect/{filename}"
        except Exception as e:
            print(f"[!] Could not save alert image: {e}")
            image_url = None

        sim_pos = simulated_mission_position()
        hazard_nearby = nearest_recent_hazard(sim_pos["x_m"], sim_pos["y_m"])
        alert_store.create(
            confidence=round(confidence, 1),
            person_count=person_count,
            source="camera-direct",
            image_url=image_url,
            lat=None,
            lon=None,
            thermal_verified=thermal_verified,
            sim_lat=sim_pos["lat"],
            sim_lon=sim_pos["lon"],
            sim_x_m=sim_pos["x_m"],
            sim_y_m=sim_pos["y_m"],
            near_hazard_type=hazard_nearby[0] if hazard_nearby else None,
            near_hazard_m=hazard_nearby[1] if hazard_nearby else None,
        )

        # Also send it over LoRa, independent of the local alert above --
        # this is what makes the detection reach a rescue team that has no
        # WiFi/hotspot link to this Pi at all, only LoRa range. Same
        # position-unknown honesty applies: None, not a fabricated 0,0 (see
        # sar.packet.HumanDetected's no-fix sentinel).
        if drone_link is not None:
            drone_link.send_detection(
                lat=None, lon=None, confidence=confidence, person_count=person_count,
            )

    def capture_snapshot(self):
        """Save a snapshot of the current frame."""
        with self.lock:
            if self.latest_frame_bgr is None:
                return None
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"snapshot_{ts}.jpg"
            save_path = RUNS_DIR / filename
            cv2.imwrite(str(save_path), self.latest_frame_bgr)
            return filename

    def start_recording(self, seconds=10):
        """Start recording video clip."""
        with self.lock:
            if self.is_recording:
                return False
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"clip_{ts}.mp4"
            save_path = RUNS_DIR / filename
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            fps_rec = max(self.fps, 10.0)
            self.record_writer = cv2.VideoWriter(
                str(save_path), fourcc, fps_rec, (640, 480)
            )
            self.record_frames_remaining = int(fps_rec * seconds)
            self.is_recording = True
            print(f"[*] Started recording to {save_path} for {seconds}s")
            return filename

    def stop(self):
        self.running = False
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception:
                pass
        if self.net_cap:
            try:
                self.net_cap.release()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Hazard classification -- fire/smoke, via a real pretrained model (not
# trained by this project). Runs as its OWN thread on a slow cadence,
# deliberately decoupled from DetectionWorker's loop: that loop is the
# safety-critical, real-time one (a person needs ~13fps tracking; this
# doesn't), and running a second full YOLO pass inline would add a
# periodic latency spike to the person-detection timing on every frame it
# ran -- exactly the kind of stutter this project has already fought hard
# to get rid of (see the watchdog and the thermal-throttling notes
# elsewhere in this file's history). Reads DetectionWorker.latest_frame_bgr
# instead of grabbing the camera a second time; Picamera2 only supports one
# capture session anyway.
#
# Model: YOLOv8n fine-tuned on a public fire/smoke dataset (not trained
# in-house, unlike best.pt) -- see hazard_fire_smoke.pt's source:
# https://github.com/luminous0219/fire-and-smoke-detection-yolov8
# (AGPL-3.0; trained on https://universe.roboflow.com/fire-rqbio/fire-and-smoke-yikzn).
# Its own validation run: precision 86.9%, recall 84.9%, mAP50 92.5%,
# mAP50-95 64.0% over 150 epochs -- real numbers from that repo's
# results.csv, not measured against this project's own footage.
# --------------------------------------------------------------------------

HAZARD_MODEL_PATH = "hazard_fire_smoke.pt"
HAZARD_CHECK_INTERVAL_S = 2.0
HAZARD_CONF = 0.35
"""Higher than the person model's 0.25: a missed fire is bad, but so is a
dashboard that cries "FIRE" at an orange sunset through a window often
enough that the rescue team stops trusting this feed. Not measured against
real footage here -- re-tune once there is some."""
HAZARD_IMGSZ = 640  # matches this model's own training imgsz (args.yaml) -- see MODEL_REGISTRY's
                     # comment block above on why imgsz must match what a model was actually trained/exported at


class HazardWorker:
    def __init__(self, detection_worker: "DetectionWorker") -> None:
        self.detection_worker = detection_worker
        self.model = None
        self.running = True
        self.lock = threading.Lock()
        self.latest_hazards: list[dict] = []  # what's in frame right now, refreshed each check
        self.total_checks = 0
        self._last_check_at = 0.0
        self._last_event_at: dict[str, float] = {}  # hazard_type -> monotonic time of last raised event
        self._load_model()

    def _load_model(self) -> None:
        try:
            self.model = YOLO(HAZARD_MODEL_PATH, task="detect")
            print(f"[+] Hazard model loaded: {HAZARD_MODEL_PATH} (classes: {self.model.names})")
        except Exception as e:
            print(f"[!] Hazard model not available ({e}) -- hazard classification disabled")
            self.model = None

    def run(self) -> None:
        while self.running:
            time.sleep(0.5)
            if self.model is None:
                continue
            now = time.monotonic()
            if now - self._last_check_at < HAZARD_CHECK_INTERVAL_S:
                continue
            self._last_check_at = now

            with self.detection_worker.lock:
                frame = self.detection_worker.latest_frame_bgr
            if frame is None:
                continue
            frame = frame.copy()  # predict() below must not race the capture loop's next write

            try:
                results = self.model.predict(
                    source=frame, conf=HAZARD_CONF, imgsz=HAZARD_IMGSZ, device="cpu", verbose=False,
                )
                res = results[0]
                self.total_checks += 1
                hazards = []
                for box, cls_id, conf in zip(
                    res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy(), res.boxes.conf.cpu().numpy()
                ):
                    hazards.append({
                        "type": self.model.names[int(cls_id)],
                        "confidence": float(conf) * 100.0,
                        "bbox": [int(v) for v in box],
                    })
                with self.lock:
                    self.latest_hazards = hazards
                for h in hazards:
                    self._maybe_raise_event(frame, h)
            except Exception as e:
                print(f"[!] Hazard inference error: {e}")

    def _maybe_raise_event(self, frame_bgr, hazard: dict) -> None:
        htype = hazard["type"]
        last = self._last_event_at.get(htype, 0.0)
        if time.monotonic() - last < HAZARD_COOLDOWN_S:
            return
        self._last_event_at[htype] = time.monotonic()

        annotated = frame_bgr.copy()
        x1, y1, x2, y2 = hazard["bbox"]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 60, 255), 2)
        cv2.putText(annotated, f"{htype} {hazard['confidence']:.0f}%", (x1, max(20, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 60, 255), 2, cv2.LINE_AA)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"hazard_{htype}_{ts}.jpg"
        try:
            cv2.imwrite(str(RUNS_DIR / filename), annotated)
            image_url = f"/runs/detect/{filename}"
        except Exception as e:
            print(f"[!] Could not save hazard image: {e}")
            image_url = None

        sim_pos = simulated_mission_position()
        hazard_store.create(
            hazard_type=htype,
            confidence=round(hazard["confidence"], 1),
            image_url=image_url,
            sim_lat=sim_pos["lat"], sim_lon=sim_pos["lon"],
            sim_x_m=sim_pos["x_m"], sim_y_m=sim_pos["y_m"],
        )

    def stop(self) -> None:
        self.running = False


# --------------------------------------------------------------------------
# Visual odometry -- a REAL camera-derived trajectory, not the simulated
# lawnmower position above. Important to be precise about what this is:
# frame-to-frame monocular odometry (ORB features + essential-matrix pose
# recovery), not full SLAM. Concretely that means:
#   - no loop closure: small per-frame errors accumulate and the estimated
#     path silently drifts from the truth over a long mission -- there is
#     no mechanism here to notice or correct that, unlike real SLAM.
#   - no metric scale: a single camera cannot recover true distance from
#     motion alone (the classic monocular scale-ambiguity problem). Each
#     step's translation is a *direction*, scaled by VO_STEP_SCALE_M below,
#     which is a guessed constant, not a measurement. Treat the trajectory
#     as relative shape, not a metric map, unless/until a real scale
#     reference (IMU, altimeter, stereo baseline) is added.
#   - needs texture: over water, smoke, uniform ground or in motion blur
#     there aren't enough matched features to recover a pose, and tracking
#     reports itself lost rather than silently guessing -- see
#     `tracking_ok`.
#   - runs on whatever intrinsics camera_calibration.json holds, or a rough
#     pinhole default if that file doesn't exist yet (`calibrated: false`
#     in every response until calibrate_camera.py has actually been run
#     against this specific camera).
#   - only runs at all while DetectionWorker.camera_active is True. When
#     there's no real camera, DetectionWorker.latest_frame_bgr is a static
#     placeholder frame (see generate_dummy_frame) -- identical frame
#     matched against itself is a degenerate case for epipolar geometry,
#     and testing against it produced exactly what you'd expect: a fake
#     "tracking_ok" with a slow noise-driven drift and no real motion
#     behind it at all. `camera_active` is surfaced in /api/vo/status so
#     the dashboard shows "NO CAMERA" instead of a confident-looking but
#     meaningless TRACKING badge.
# --------------------------------------------------------------------------

VO_INTERVAL_S = 0.15          # ~6-7 Hz -- enough to see parallax, cheap enough to share the CPU with YOLO
VO_MIN_MATCHES = 40           # below this, pose recovery is too noisy to trust -- report lost instead of guessing
VO_MIN_PIXEL_MOTION = 2.5     # mean inlier pixel displacement below this = noise, not real motion -- see _process()
VO_MAX_TRAJECTORY_POINTS = 4000
VO_STEP_SCALE_M = 0.5         # guessed per-step distance -- see module comment above; NOT a measurement
VO_CALIBRATION_PATH = "camera_calibration.json"


def _load_camera_intrinsics(resolution: tuple[int, int]) -> tuple[np.ndarray, bool]:
    """Real intrinsics from camera_calibration.json if calibrate_camera.py
    has been run against this camera; otherwise a rough pinhole guess from
    resolution + an assumed ~62-degree horizontal FOV (typical for Pi
    camera modules). The guess is good enough to get a plausible-looking
    trajectory but is NOT calibrated -- callers must surface `calibrated`
    honestly rather than trusting either case the same way.
    """
    w, h = resolution
    path = Path(VO_CALIBRATION_PATH)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            fx, fy, cx, cy = data["fx"], data["fy"], data["cx"], data["cy"]
            return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64), True
        except Exception as e:
            print(f"[!] camera_calibration.json unreadable ({e}) -- falling back to uncalibrated estimate")
    assumed_hfov_deg = 62.0
    fx = fy = (w / 2.0) / math.tan(math.radians(assumed_hfov_deg / 2.0))
    cx, cy = w / 2.0, h / 2.0
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64), False


class VisualOdometryWorker:
    """Frame-to-frame monocular visual odometry over DetectionWorker's own
    frames -- see the module comment above for exactly what this is and
    isn't. Mirrors HazardWorker's shape: a decoupled, slower-cadence thread
    reading DetectionWorker's shared frame state rather than opening a
    second capture session (Picamera2 only supports one).

    Reads latest_raw_frame_bgr (from _capture_loop, ~STREAM_TARGET_FPS),
    not latest_frame_bgr (from run(), the YOLO inference loop). Originally
    this read latest_frame_bgr, back when capture and inference were one
    loop and both updated at the same rate; after they were split (see
    _capture_loop's docstring), inference dropped to a variable, often
    slower cadence under load -- sometimes below this worker's own
    VO_INTERVAL_S -- so sampling latest_frame_bgr here meant frequently
    re-reading the SAME frame, i.e. comparing an image against itself,
    which reports zero motion and misses real small movements between
    inference updates. latest_raw_frame_bgr updates independently of
    inference, so a real (even small) movement of the camera is much more
    likely to actually appear between two consecutive samples here.
    """

    def __init__(self, detection_worker: "DetectionWorker") -> None:
        self.detection_worker = detection_worker
        self.running = True
        self.lock = threading.Lock()

        self.orb = cv2.ORB_create(nfeatures=800)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

        self._prev_gray = None
        self._prev_kp = None
        self._prev_des = None
        self._last_frame_seen = None  # identity check below -- see run()

        self.vo_frame_size = (640, 480)  # (w, h) -- must match _load_camera_intrinsics below; _process resizes every frame to this before feature detection, so a capture-resolution change elsewhere (e.g. a sharper USB webcam) can't silently desync pixel coordinates from K
        self.K, self.calibrated = _load_camera_intrinsics(self.vo_frame_size)

        # Cumulative pose: position on an arbitrary relative-meters plane,
        # heading in radians. Both start at zero by definition -- this
        # mission's own starting point, not any real-world coordinate.
        self._x_m = 0.0
        self._y_m = 0.0
        self._heading_rad = 0.0

        self.trajectory: list[dict] = []
        self.tracking_ok = False
        self.matched_features = 0
        self.frames_processed = 0
        self._last_run_at = 0.0
        self._append_point(quality="init")

    def _append_point(self, quality: str) -> None:
        self.trajectory.append({
            "x_m": round(self._x_m, 2),
            "y_m": round(self._y_m, 2),
            "t": time.time(),
            "quality": quality,
        })
        if len(self.trajectory) > VO_MAX_TRAJECTORY_POINTS:
            self.trajectory = self.trajectory[-VO_MAX_TRAJECTORY_POINTS:]

    def run(self) -> None:
        while self.running:
            time.sleep(0.05)
            now = time.monotonic()
            if now - self._last_run_at < VO_INTERVAL_S:
                continue
            self._last_run_at = now

            if not self.detection_worker.camera_active:
                # No real camera -- latest_frame_bgr is a static placeholder
                # (see generate_dummy_frame), and tracking against it is
                # meaningless drift, not a real signal. Drop the stale
                # previous-frame reference so it doesn't get matched against
                # whatever real frame arrives first once a camera connects.
                self._prev_gray, self._prev_kp, self._prev_des = None, None, None
                with self.lock:
                    self.tracking_ok = False
                    self.matched_features = 0
                continue

            with self.detection_worker.lock:
                frame = self.detection_worker.latest_raw_frame_bgr
            if frame is None:
                continue
            if frame is self._last_frame_seen:
                continue  # _capture_loop hasn't produced a new one since we last looked -- would just match against itself
            self._last_frame_seen = frame
            frame = frame.copy()  # match HazardWorker: predict/compute below must not race the capture loop

            # self.K was built for vo_frame_size -- resizing here keeps
            # feature pixel coordinates consistent with it regardless of
            # whatever resolution the camera actually captures at (see
            # vo_frame_size's comment). This also keeps ORB's per-frame
            # cost fixed even if capture resolution changes elsewhere.
            if (frame.shape[1], frame.shape[0]) != self.vo_frame_size:
                frame = cv2.resize(frame, self.vo_frame_size)

            try:
                self._process(frame)
            except Exception as e:
                print(f"[!] Visual odometry error: {e}")

    def _process(self, frame_bgr) -> None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        kp, des = self.orb.detectAndCompute(gray, None)
        self.frames_processed += 1

        if self._prev_des is None or des is None or len(kp) < VO_MIN_MATCHES:
            self._prev_gray, self._prev_kp, self._prev_des = gray, kp, des
            with self.lock:
                self.tracking_ok = False
                self.matched_features = 0 if des is None else len(kp)
            return

        matches = self.matcher.match(self._prev_des, des)
        matches = sorted(matches, key=lambda m: m.distance)[:200]

        if len(matches) < VO_MIN_MATCHES:
            self._prev_gray, self._prev_kp, self._prev_des = gray, kp, des
            with self.lock:
                self.tracking_ok = False
                self.matched_features = len(matches)
            return

        pts_prev = np.float32([self._prev_kp[m.queryIdx].pt for m in matches])
        pts_cur = np.float32([kp[m.trainIdx].pt for m in matches])

        E, mask = cv2.findEssentialMat(
            pts_cur, pts_prev, self.K, method=cv2.RANSAC, prob=0.999, threshold=1.0,
        )
        self._prev_gray, self._prev_kp, self._prev_des = gray, kp, des

        if E is None or E.shape != (3, 3):
            with self.lock:
                self.tracking_ok = False
                self.matched_features = len(matches)
            return

        inliers = int(mask.sum()) if mask is not None else 0
        if inliers < VO_MIN_MATCHES:
            with self.lock:
                self.tracking_ok = False
                self.matched_features = inliers
            return

        # Gate on actual pixel displacement before trusting a pose at all.
        # recoverPose's translation is always unit-length -- monocular
        # scale is fundamentally unrecoverable -- so without this check, a
        # perfectly stationary camera with nothing but sensor/JPEG noise on
        # its matched keypoints still "recovers" a confident pose and would
        # add a full VO_STEP_SCALE_M phantom step every single frame. That
        # is not the slow, expected drift the module comment describes --
        # it is meters of fake motion per second sitting still. Below this
        # threshold, treat it as tracking a static scene: keep the position
        # exactly where it was rather than integrating noise as movement.
        inlier_mask = mask.ravel().astype(bool)
        pixel_disp = float(np.linalg.norm(pts_cur[inlier_mask] - pts_prev[inlier_mask], axis=1).mean())
        if pixel_disp < VO_MIN_PIXEL_MOTION:
            with self.lock:
                self.tracking_ok = True
                self.matched_features = inliers
                self._append_point(quality="stationary")
            return

        _, R, t, _ = cv2.recoverPose(E, pts_cur, pts_prev, self.K, mask=mask)

        # t is a unit-length direction in the camera frame (x-right,
        # y-down, z-forward); only the ground-plane component (camera
        # x/z) draws the 2D trail, scaled by the guessed VO_STEP_SCALE_M --
        # see the module comment on why that scale is not a measurement.
        dx_cam = float(t[0][0])
        dz_cam = float(t[2][0])
        step = math.hypot(dx_cam, dz_cam)
        if step > 1e-6:
            dx_cam, dz_cam = dx_cam / step, dz_cam / step
        dyaw = math.atan2(float(R[0][2]), float(R[2][2]))

        with self.lock:
            self._heading_rad += dyaw
            self._x_m += (dx_cam * math.cos(self._heading_rad) + dz_cam * math.sin(self._heading_rad)) * VO_STEP_SCALE_M
            self._y_m += (dz_cam * math.cos(self._heading_rad) - dx_cam * math.sin(self._heading_rad)) * VO_STEP_SCALE_M
            self.tracking_ok = True
            self.matched_features = inliers
            self._append_point(quality="ok")

    def status(self) -> dict:
        with self.lock:
            return {
                "camera_active": self.detection_worker.camera_active if self.detection_worker else False,
                "tracking_ok": self.tracking_ok,
                "matched_features": self.matched_features,
                "frames_processed": self.frames_processed,
                "calibrated": self.calibrated,
                "trajectory_points": len(self.trajectory),
                "position": {"x_m": round(self._x_m, 2), "y_m": round(self._y_m, 2)},
            }

    def recent_trajectory(self, limit: int = 500) -> list[dict]:
        with self.lock:
            return list(self.trajectory[-limit:])

    def stop(self) -> None:
        self.running = False


LOCATION_STALE_AFTER_S = 600.0  # a fix older than this is treated as no fix -- see status()


class LocationWorker:
    """Real position via WiFi geolocation -- see GOOGLE_GEOLOCATION_API_KEY's
    module comment for why this exists and what was ruled out first.
    Mirrors HazardWorker's shape: a decoupled, slow-cadence thread that is a
    silent no-op when its dependency (here, an API key) isn't configured,
    same as HazardWorker degrading gracefully when its model file is
    missing. Never touches Alert.lat/lon -- see the comment on those
    fields; a real WiFi-geolocation fix is precise enough to expose on the
    Command Center map (api/mission/position), same tier of trust the
    lawnmower simulation already had there, but it is not a GPS fix on the
    survivor and must not be presented as one.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = True
        self.lat: float | None = None
        self.lon: float | None = None
        self.accuracy_m: float | None = None
        self.obtained_at: float | None = None
        self.last_error: str | None = None

    def _scan_wifi(self) -> list[dict]:
        """Nearby access points as Google's API wants them: [{macAddress,
        signalStrength}, ...]. Needs passwordless sudo for `iw scan`
        (already the case on this Pi -- verified with `sudo -n iw dev
        wlan0 scan` before writing this)."""
        result = subprocess.run(
            ["sudo", "-n", "iw", "dev", WIFI_INTERFACE, "scan"],
            capture_output=True, text=True, timeout=15.0,
        )
        if result.returncode != 0:
            raise RuntimeError(f"iw scan failed: {result.stderr.strip()[:200]}")
        aps = []
        mac = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("BSS "):
                mac = line.split()[1].split("(")[0]
            elif line.startswith("signal:") and mac:
                signal_dbm = float(line.split()[1])
                aps.append({"macAddress": mac, "signalStrength": int(round(signal_dbm))})
                mac = None
        return aps

    def _fetch_fix(self) -> None:
        aps = self._scan_wifi()
        if len(aps) < 2:
            raise RuntimeError(f"only {len(aps)} access point(s) seen -- too few for a reliable fix")

        # considerIp=False deliberately: if the WiFi lookup can't resolve,
        # Google will otherwise silently fall back to IP-based geolocation
        # -- the same city-level result already ruled out as "not precise"
        # (see GOOGLE_GEOLOCATION_API_KEY's comment) -- and this method has
        # no way to tell the two apart in the response. Better to fail
        # loudly here and fall back to the honestly-labelled simulation
        # than to silently serve an imprecise fix as if it were the precise
        # one this feature exists for.
        res = requests.post(
            f"https://www.googleapis.com/geolocation/v1/geolocate?key={GOOGLE_GEOLOCATION_API_KEY}",
            json={"considerIp": False, "wifiAccessPoints": aps},
            timeout=10.0,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Google Geolocation API returned {res.status_code}: {res.text[:200]}")
        data = res.json()
        loc = data["location"]
        with self.lock:
            self.lat = loc["lat"]
            self.lon = loc["lng"]
            self.accuracy_m = data.get("accuracy")
            self.obtained_at = time.time()
            self.last_error = None

    def run(self) -> None:
        while self.running:
            if not GOOGLE_GEOLOCATION_API_KEY:
                time.sleep(5.0)  # cheap poll for the key appearing without a restart isn't worth it -- just idle
                continue
            try:
                self._fetch_fix()
                # Broadcast the fresh fix over the real LoRa link too, not
                # just into this process's own state -- see
                # lora_uplink_direct.py's send_telemetry() and
                # lora_bridge.py's report_telemetry(). A real, physically
                # separate GCS Pi has no other way to learn this.
                if drone_link is not None:
                    drone_link.send_telemetry(self.lat, self.lon)
                sleep_s = LOCATION_UPDATE_INTERVAL_S
            except Exception as e:
                with self.lock:
                    self.last_error = "Wi-Fi location lookup failed (" + type(e).__name__ + ")"
                print("[!] " + self.last_error)
                # A failed attempt is usually a momentary hiccup (the `iw
                # scan` subprocess missing its 15s timeout under a CPU spike,
                # e.g. from a burst of detections -- observed in practice,
                # not hypothetical), not a standing outage. Retrying much
                # sooner than the normal cadence clears it quickly instead
                # of leaving both LocationWorker and, downstream, the LoRa
                # telemetry beacon dark for a full cycle over what's usually
                # a one-off. Google's quota only takes the hit on the retry
                # actually needed, not on every idle cycle.
                sleep_s = LOCATION_RETRY_INTERVAL_S
            time.sleep(sleep_s)

    def status(self) -> dict:
        """None fields (not a 'simulated: false' with stale numbers) is
        what tells api_mission_position() to fall back honestly -- same
        staleness contract as mesh_status.connected."""
        with self.lock:
            fresh = self.obtained_at is not None and (time.time() - self.obtained_at) <= LOCATION_STALE_AFTER_S
            if not fresh:
                return {"available": False, "last_error": self.last_error or ("Google Geolocation key is not configured" if not GOOGLE_GEOLOCATION_API_KEY else "Waiting for Wi-Fi location")}
            return {
                "available": True,
                "lat": self.lat,
                "lon": self.lon,
                "accuracy_m": self.accuracy_m,
                "seconds_since_fix": round(time.time() - self.obtained_at, 1),
            }

    def stop(self) -> None:
        self.running = False


TEAM_LOCATION_STALE_AFTER_S = 60.0
"""A rescuer's phone reports every ~15s while the app is in the foreground
(see mobile_app/lib/services/location_service.dart); a minute of silence
most likely means the app was closed or backgrounded, not that the
rescuer teleported away -- treated as 'not currently known', not shown as
a stale position on the map."""


class TeamLocationStore:
    """Live GPS positions self-reported by rescue-team phones running the
    mobile app -- POSTed to /api/team/location every time the app gets a
    fresh fix. Bounded by nothing -- a real deployment has a handful of
    phones, not thousands -- and, like MeshStatus, holds only the latest
    reading per member, not a history.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.members: dict[str, dict] = {}  # member_id -> {name, lat, lon, updated_at}

    def update(self, member_id: str, name: str, lat: float, lon: float) -> None:
        with self.lock:
            self.members[member_id] = {"name": name or member_id, "lat": lat, "lon": lon, "updated_at": time.time()}

    def active_members(self) -> list[dict]:
        """Members heard from within TEAM_LOCATION_STALE_AFTER_S. Not
        sorted by distance -- callers that want "nearest" do that
        themselves against whatever point they're measuring from."""
        now = time.time()
        with self.lock:
            return [
                {**m, "member_id": mid, "seconds_since_update": round(now - m["updated_at"], 1)}
                for mid, m in self.members.items()
                if (now - m["updated_at"]) <= TEAM_LOCATION_STALE_AFTER_S
            ]


DRONE_TELEMETRY_STALE_AFTER_S = 3 * LOCATION_UPDATE_INTERVAL_S
"""3x the send interval, not 1x: a beacon queued every
LOCATION_UPDATE_INTERVAL_S still has to clear the detection queue ahead of
it, cross the air interface, and get relayed/POSTed before this store
sees it, so a bound equal to the interval itself leaves no slack -- the
store would flicker to "stale" and back right before every single beacon
lands. Same reasoning as TEAM_LOCATION_STALE_AFTER_S's 4x margin over the
phone's 15s report interval, just a smaller multiplier here because
LOCATION_UPDATE_INTERVAL_S is already much coarser."""


class DroneTelemetryStore:
    """The drone's own position, as it actually arrived over the LoRa
    link (see lora_bridge.py's report_telemetry, fed from a real
    MsgType.TELEMETRY frame -- sar/packet.py's Telemetry payload). Holds
    only the latest reading, same shape as TeamLocationStore, for the same
    reason: this is "where is the drone right now", not a track history.

    /api/team/nearest reads this instead of calling location_worker
    directly, so that route reflects what a real, physically separate GCS
    Pi would actually have to work with -- on today's single-Pi bench
    setup the two numbers happen to agree (see LocationWorker.run(), which
    is what feeds this store's only input), but only one of them is true
    once the drone is flying with its own Pi.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.lat: float | None = None
        self.lon: float | None = None
        self.alt_m: int | None = None
        self.updated_at: float | None = None

    def update(self, lat: float, lon: float, alt_m: int | None = None) -> None:
        with self.lock:
            self.lat = lat
            self.lon = lon
            self.alt_m = alt_m
            self.updated_at = time.time()

    def status(self) -> dict:
        with self.lock:
            fresh = self.updated_at is not None and (time.time() - self.updated_at) <= DRONE_TELEMETRY_STALE_AFTER_S
            if not fresh:
                return {"available": False}
            return {
                "available": True,
                "lat": self.lat,
                "lon": self.lon,
                "seconds_since_update": round(time.time() - self.updated_at, 1),
            }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters. Unlike _meters_to_latlon's flat-
    earth approximation (fine only at the simulated map's city-block
    scale), a rescuer-to-survivor distance is a real-world figure that can
    genuinely span kilometers, where flat-earth error compounds -- this is
    the formula that stays accurate at that scale."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# Global worker instance
worker = DetectionWorker()
hazard_worker = HazardWorker(worker)
vo_worker = VisualOdometryWorker(worker)
location_worker = LocationWorker()
team_location_store = TeamLocationStore()
drone_telemetry_store = DroneTelemetryStore()

# Global LoRa uplink. Constructing it is always safe (see the import guard
# above) -- it only actually brings the radio up once .start() runs its
# background thread, and even then a missing/misconfigured RA-02 just logs
# a warning rather than raising. Defaults (SPI0.0, reset=GPIO25,
# dio0=GPIO24, 500kHz) match this Pi's actual wiring, already verified by
# tools/checkradio.py.
drone_link = DirectRadioUplink() if HAS_LORA_UPLINK else None


def read_system_telemetry():
    """Read CPU temperature, CPU usage, and memory on Raspberry Pi 5."""
    temp = 0.0
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = float(f.read().strip()) / 1000.0
    except Exception:
        pass

    disk = psutil.disk_usage("/")
    return {
        "cpu_temp": round(temp, 1),
        "cpu_usage": round(psutil.cpu_percent(), 1),
        "ram_usage": round(psutil.virtual_memory().percent, 1),
        "ram_used_gb": round(psutil.virtual_memory().used / 1e9, 1),
        "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "disk_usage": round(disk.percent, 1),
        "disk_total_gb": round(disk.total / 1e9, 0),
    }


# psutil.cpu_percent is sampled on ONE persistent thread, not a fresh HTTP thread.
_telemetry = read_system_telemetry()


def sample_system_telemetry():
    global _telemetry
    while worker.running:
        _telemetry = read_system_telemetry()
        time.sleep(1)


def get_system_telemetry():
    return dict(_telemetry)


from rescue_location import register_location_routes
register_location_routes(app, location_worker)

ARGUS_DIST_DIR = Path(__file__).parent / "argus_dist"


@app.route("/")
def index():
    """ARGUS command center -- the real frontend (React, built static,
    served from argus_dist/). Falls back to the old landing hub if that
    build hasn't been deployed yet.

    index.html is explicitly never cached: it's the one file whose name
    never changes between deploys (it just points at whichever content-
    hashed JS/CSS filenames the latest build produced), so a browser that
    cached an old copy keeps loading an old *script tag* forever -- every
    redeploy this session that seemed to "not take" was this, not a real
    bug. The hashed /assets/* files below are the opposite: safe to cache
    forever, since any content change gives them a new filename.
    """
    if (ARGUS_DIST_DIR / "index.html").exists():
        resp = send_from_directory(ARGUS_DIST_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return render_template("index.html")


@app.route("/assets/<path:filename>")
def argus_assets(filename):
    return send_from_directory(ARGUS_DIST_DIR / "assets", filename, max_age=31536000)


@app.route("/legacy")
def legacy_index():
    """Original landing hub -- links out to the two mission dashboards."""
    return render_template("index.html")


def fresh_frame_stream(thermal=False):
    """Each client takes the newest encoded frame; slow readers skip old frames."""
    sequence = -1
    if thermal:
        with worker.lock:
            worker.thermal_clients += 1
    try:
        while worker.running:
            with worker.frame_ready:
                worker.frame_ready.wait_for(
                    lambda: not worker.running or (worker.thermal_sequence if thermal else worker.frame_sequence) != sequence,
                    timeout=2.0,
                )
                current = worker.thermal_sequence if thermal else worker.frame_sequence
                frame = worker.latest_thermal_jpeg if thermal else worker.latest_jpeg
                if not worker.running:
                    break
                if current == sequence:
                    continue
                sequence = current
                if frame is None:
                    continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(frame)).encode() + b"\r\nX-Frame-Sequence: "
                   + str(sequence).encode() + b"\r\n\r\n" + frame + b"\r\n")
    finally:
        if thermal:
            with worker.lock:
                worker.thermal_clients = max(0, worker.thermal_clients - 1)


@app.route("/video_feed")
def video_feed():
    return Response(fresh_frame_stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.route("/video_feed_thermal")
def video_feed_thermal():
    return Response(fresh_frame_stream(thermal=True), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.route("/api/stats")
def api_stats():
    """Return real-time telemetry stats."""
    sys_stats = get_system_telemetry()
    with worker.lock:
        entry = MODEL_REGISTRY.get(worker.model_name, {})
        data = {
            "fps": round(worker.fps, 1),
            "stream_fps": round(worker.stream_fps, 1),
            "latency_ms": round(worker.latency_ms, 1),
            "persons_detected": worker.persons_detected,
            "persons_verified": worker.persons_verified,
            "total_frames": worker.total_frames,
            "model": worker.model_name,
            "model_short": entry.get("engine", "?"),
            "inference_threads": getattr(worker, "inference_threads", None),
            "model_label": entry.get("label", worker.model_name),
            "imgsz": worker.imgsz,
            "conf": worker.conf,
            "is_recording": worker.is_recording,
            "cpu_temp": sys_stats["cpu_temp"],
            "cpu_usage": sys_stats["cpu_usage"],
            "ram_usage": sys_stats["ram_usage"],
            "ram_used_gb": sys_stats["ram_used_gb"],
            "ram_total_gb": sys_stats["ram_total_gb"],
            "disk_usage": sys_stats["disk_usage"],
            "disk_total_gb": sys_stats["disk_total_gb"],
            "camera_active": worker.camera_active,
            "camera_source": worker.camera_source,
            "frame_sequence": worker.frame_sequence,
            "resolution": list(worker.latest_raw_frame_bgr.shape[1::-1]) if worker.latest_raw_frame_bgr is not None else None,
            "uptime_s": time.time() - worker.start_time,
        }
    return jsonify(data)


@app.route("/api/config", methods=["POST"])
def api_config():
    """Update runtime configuration.

    imgsz is intentionally NOT settable here. It used to be an independent
    field the client could set to any value regardless of which model was
    loaded -- selecting NCNN (fixed at 320) and then "640x640 (High Res)"
    silently ran the network at 4x its exported cost with no error, which is
    almost certainly what "camera is lagging" was. imgsz now always comes
    from MODEL_REGISTRY via load_model(); to change resolution, select a
    different model.
    """
    data = request.json or {}
    new_model = data.get("model")
    new_conf = data.get("conf")

    if new_model and new_model != worker.model_name:
        if new_model not in MODEL_REGISTRY:
            return jsonify({
                "status": "error",
                "message": f"Unknown model '{new_model}'. Choose one of: {list(MODEL_REGISTRY)}",
            }), 400
        worker.load_model(new_model)

    if new_conf is not None:
        with worker.lock:
            worker.conf = float(new_conf)

    return jsonify({
        "status": "ok",
        "model": worker.model_name,
        "model_label": MODEL_REGISTRY[worker.model_name]["label"],
        "imgsz": worker.imgsz,
        "conf": worker.conf,
    })


@app.route("/api/models")
def api_models():
    """List the selectable models, so the UI never has to hard-code them."""
    return jsonify({
        "current": worker.model_name,
        "models": [
            {"id": name, **entry} for name, entry in MODEL_REGISTRY.items()
        ],
    })


@app.route("/api/snapshot", methods=["POST"])
def api_snapshot():
    """Capture and save snapshot."""
    filename = worker.capture_snapshot()
    if filename:
        return jsonify({"status": "ok", "filename": filename, "url": f"/runs/detect/{filename}"})
    return jsonify({"status": "error", "message": "Failed to capture snapshot"}), 500


@app.route("/api/record", methods=["POST"])
def api_record():
    """Record 10s video clip."""
    filename = worker.start_recording(seconds=10)
    if filename:
        return jsonify({"status": "ok", "filename": filename})
    return jsonify({"status": "error", "message": "Already recording"}), 400


@app.route("/api/gallery")
def api_gallery():
    """List captured snapshots and clips."""
    items = []
    for f in sorted(RUNS_DIR.glob("*.*"), key=os.path.getmtime, reverse=True):
        if f.suffix.lower() in [".jpg", ".jpeg", ".png"]:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M:%S")
            items.append({
                "filename": f.name,
                "url": f"/runs/detect/{f.name}",
                "type": "image",
                "date": mtime,
            })
        elif f.suffix.lower() in [".mp4", ".avi"]:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M:%S")
            items.append({
                "filename": f.name,
                "url": f"/runs/detect/{f.name}",
                "type": "video",
                "date": mtime,
            })
    return jsonify(items)


@app.route("/runs/detect/<path:filename>")
def serve_media(filename):
    """Serve detection snapshots and video files."""
    return send_from_directory(str(RUNS_DIR), filename)


# --------------------------------------------------------------------------
# Emergency alert gateway -- what the rescue-team mobile app talks to.
#
# This is the one API surface meant to outlive whichever detection/mesh
# backend ends up "winning" (see the memory note on this project: there are
# three overlapping systems on this Pi right now). Every consumer -- the
# Flutter app, a future web console, anything else -- only needs this
# schema. A LoRa-relayed alert from the actual flying drone (once the mesh
# is wired in) lands here through /api/alerts/ingest looking identical to
# one this Pi's own camera raised through _raise_alert(); nothing
# downstream has to know which happened.
# --------------------------------------------------------------------------


@app.route("/api/alerts")
def api_alerts():
    """Alert history, most recent first. ?since=<id> for only what's new."""
    since = request.args.get("since")
    alerts = alert_store.list_since(since)
    return jsonify([asdict(a) for a in reversed(alerts)])


@app.route("/api/alerts/stream")
def api_alerts_stream():
    """Server-sent events: push each new/updated alert the instant it happens.

    This is what gives the phone app an "emergency alert" rather than
    something it has to notice by polling. A heartbeat comment keeps the
    connection alive through NATs and mobile carriers that close idle
    sockets, and lets the client detect a dead connection quickly instead of
    waiting on a TCP timeout.
    """
    def generate():
        q = alert_store.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    alert = q.get(timeout=15.0)
                    yield f"data: {json.dumps(asdict(alert))}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            alert_store.unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",  # don't let a reverse proxy buffer the stream
    })


@app.route("/api/hazards")
def api_hazards():
    """Hazard event history, most recent first. ?since=<id> for only what's new."""
    since = request.args.get("since")
    events = hazard_store.list_since(since)
    return jsonify([asdict(e) for e in reversed(events)])


@app.route("/api/hazards/stream")
def api_hazards_stream():
    """Server-sent events for hazard sightings -- same pattern as /api/alerts/stream."""
    def generate():
        q = hazard_store.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = q.get(timeout=15.0)
                    yield f"data: {json.dumps(asdict(event))}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            hazard_store.unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
    })


@app.route("/api/hazards/active")
def api_hazards_active():
    """What the hazard model currently sees in frame -- refreshed every
    HAZARD_CHECK_INTERVAL_S, separate from the historical event log above
    (which is cooldown-gated and persisted). This is the live "what's
    burning right now" read, the hazard equivalent of /api/stats's
    persons_detected."""
    with hazard_worker.lock:
        hazards = list(hazard_worker.latest_hazards)
    return jsonify({
        "model_loaded": hazard_worker.model is not None,
        "total_checks": hazard_worker.total_checks,
        "hazards": hazards,
    })


@app.route("/api/alerts/<alert_id>/status", methods=["POST"])
def api_alert_status(alert_id):
    """Rescue-team action on one alert: acknowledge / resolve / dismiss.

    No auth here -- this is a hackathon prototype on a trusted local/mesh
    network, not a production system. Don't expose this port to the open
    Internet as-is.
    """
    data = request.json or {}
    status = data.get("status")
    if status not in ("acknowledged", "resolved", "false_alarm", "new"):
        return jsonify({"status": "error", "message": "invalid status"}), 400

    updated = alert_store.update_status(alert_id, status, data.get("by"))
    if updated is None:
        return jsonify({"status": "error", "message": "unknown alert id"}), 404
    return jsonify(asdict(updated))


@app.route("/api/alerts/ingest", methods=["POST"])
def api_alerts_ingest():
    """External ingest point for alerts NOT from this Pi's own camera --
    in particular, a detection relayed in over the LoRa mesh from the
    actual flying drone. Whatever process is receiving mesh packets calls
    this with the same fields _raise_alert() would have filled in, and it
    fans out to the app exactly the same way a local detection would.
    """
    data = request.json or {}
    required = {"confidence", "person_count"}
    if not required.issubset(data):
        return jsonify({"status": "error", "message": f"missing fields: {required - set(data)}"}), 400

    sim_pos = simulated_mission_position()
    hazard_nearby = nearest_recent_hazard(sim_pos["x_m"], sim_pos["y_m"])
    alert = alert_store.create(
        confidence=float(data["confidence"]),
        person_count=int(data["person_count"]),
        source=data.get("source", "lora-mesh"),
        image_url=data.get("image_url"),
        lat=data.get("lat"),
        lon=data.get("lon"),
        route=data.get("route"),
        hops=data.get("hops"),
        rssi_dbm=data.get("rssi_dbm"),
        sim_lat=sim_pos["lat"],
        sim_lon=sim_pos["lon"],
        sim_x_m=sim_pos["x_m"],
        sim_y_m=sim_pos["y_m"],
        near_hazard_type=hazard_nearby[0] if hazard_nearby else None,
        near_hazard_m=hazard_nearby[1] if hazard_nearby else None,
    )
    return jsonify(asdict(alert)), 201


@app.route("/api/mesh/heartbeat", methods=["POST"])
def api_mesh_heartbeat():
    """lora_bridge.py calls this on every frame it decodes off the GCS
    ESP32 -- not just ones that become alerts -- so the ground-station
    dashboard can show a live channel between detections."""
    data = request.json or {}
    now = time.time()
    if mesh_status.first_heartbeat_at is None:
        mesh_status.first_heartbeat_at = now
    mesh_status.connected = True
    mesh_status.esp32_port = data.get("port", mesh_status.esp32_port)
    mesh_status.last_heartbeat_at = now
    mesh_status.last_rssi_dbm = data.get("rssi_dbm")
    mesh_status.last_snr_db = data.get("snr_db")
    mesh_status.total_frames = int(data.get("total_frames", mesh_status.total_frames))
    if data.get("is_detection"):
        mesh_status.total_detections += 1
    if int(data.get("hop", 0)) > 0:
        mesh_status.relay_total_frames += 1
        mesh_status.relay_last_seen_at = now
    return jsonify({"status": "ok"})


@app.route("/api/mesh/telemetry", methods=["POST"])
def api_mesh_telemetry():
    """lora_bridge.py calls this with the drone's position, decoded from a
    real MsgType.TELEMETRY frame heard over the air -- see
    sar/packet.py's Telemetry payload and lora_uplink_direct.py's
    send_telemetry(). Feeds drone_telemetry_store, which /api/team/nearest
    prefers over location_worker's in-process reading."""
    data = request.json or {}
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        return jsonify({"status": "error", "message": "lat, lon are required"}), 400
    drone_telemetry_store.update(float(lat), float(lon), data.get("alt_m"))
    return jsonify({"status": "ok"})


@app.route("/api/mesh/status")
def api_mesh_status():
    """Ground-station dashboard polls this for radio link health. `connected`
    goes false once MESH_STALE_AFTER_S passes with no heartbeat, even though
    the last-known RSSI/SNR values are still returned -- the UI shows them
    greyed out as history, not as a live reading."""
    age = (time.time() - mesh_status.last_heartbeat_at) if mesh_status.last_heartbeat_at else None
    live = age is not None and age <= MESH_STALE_AFTER_S
    return jsonify({
        "connected": live,
        "esp32_port": mesh_status.esp32_port,
        "seconds_since_heartbeat": age,
        "last_rssi_dbm": mesh_status.last_rssi_dbm,
        "last_snr_db": mesh_status.last_snr_db,
        "total_frames": mesh_status.total_frames,
        "total_detections": mesh_status.total_detections,
        "uptime_s": (time.time() - mesh_status.first_heartbeat_at) if mesh_status.first_heartbeat_at else None,
        "relay_total_frames": mesh_status.relay_total_frames,
        "relay_seconds_since_seen": (time.time() - mesh_status.relay_last_seen_at) if mesh_status.relay_last_seen_at else None,
    })


@app.route("/api/mission/position")
def api_mission_position():
    """Real position when LocationWorker has a fresh WiFi-geolocation fix
    (see its class comment); otherwise the same simulated lawnmower
    position as always -- see simulated_mission_position()'s module
    comment. `simulated` is not decorative; nothing consuming this
    response may drop it, and its meaning doesn't change here: it still
    marks whether lat/lon are a real fix or not, same contract as before
    this field could ever be real."""
    loc = location_worker.status()
    if loc["available"]:
        x_m, y_m = _latlon_to_meters(loc["lat"], loc["lon"])
        return jsonify({
            "simulated": False,
            "lat": loc["lat"],
            "lon": loc["lon"],
            "accuracy_m": loc["accuracy_m"],
            "seconds_since_fix": loc["seconds_since_fix"],
            # Clamped, not remapped: a real fix is placed on the SAME map
            # frame the simulated pattern uses (see _latlon_to_meters), so
            # it can be off-center or even off the edge of MISSION_AREA_M
            # if the real location differs from the arbitrary simulated
            # origin -- clamping keeps the marker on-screen rather than
            # silently repositioning what "the map" means.
            "x_m": round(max(0.0, min(MISSION_AREA_M, x_m)), 1),
            "y_m": round(max(0.0, min(MISSION_AREA_M, y_m)), 1),
            "area_m": MISSION_AREA_M,
            "origin_lat": MISSION_ORIGIN_LAT,
            "origin_lon": MISSION_ORIGIN_LON,
            "coverage_pct": simulated_coverage_pct(),
        })

    pos = simulated_mission_position()
    return jsonify({
        "simulated": True,
        "lat": pos["lat"],
        "lon": pos["lon"],
        "x_m": pos["x_m"],
        "y_m": pos["y_m"],
        "area_m": MISSION_AREA_M,
        "origin_lat": MISSION_ORIGIN_LAT,
        "origin_lon": MISSION_ORIGIN_LON,
        "coverage_pct": simulated_coverage_pct(),
    })


@app.route("/api/team/location", methods=["POST"])
def api_team_location():
    """A rescue-team phone reports its current GPS fix -- see
    mobile_app/lib/services/location_service.dart. No auth, same trust
    model as /api/alerts/ingest: a trusted local/mesh network prototype,
    not exposed to the open internet as-is."""
    data = request.json or {}
    member_id = data.get("member_id")
    lat = data.get("lat")
    lon = data.get("lon")
    if not member_id or lat is None or lon is None:
        return jsonify({"status": "error", "message": "member_id, lat, lon are required"}), 400
    team_location_store.update(member_id, data.get("name", ""), float(lat), float(lon))
    return jsonify({"status": "ok"})


@app.route("/api/team/nearest")
def api_team_nearest():
    """Distance from the nearest active rescue-team phone to the drone's
    own real position -- used as a stand-in for "where the identified
    survivor is": an individual camera detection has no GPS fix of its own
    (see Alert.lat/lon's comment -- this Pi has no GPS or Pixhawk link to
    attach one), but the camera doing the detecting is only ever a few
    meters from whatever it's looking at, so the drone's own real position
    is a reasonable proxy at the scale a rescue team walks at.

    Prefers drone_telemetry_store -- the position as it actually arrived
    over the LoRa link (see /api/mesh/telemetry) -- and falls back to
    location_worker's in-process reading only if no telemetry frame has
    been heard yet. On today's single-Pi bench setup those two numbers
    are the same value anyway (see LocationWorker.run()); once the drone
    is flying with its own Pi, only the telemetry path will ever have
    anything to report. `position_source` says honestly which one
    answered so nothing downstream has to guess.

    Honestly unavailable, not silently wrong, when either side of that
    distance isn't real: no drone position yet, or no rescue-team phone
    currently reporting.
    """
    members = team_location_store.active_members()

    telemetry = drone_telemetry_store.status()
    if telemetry["available"]:
        drone_lat, drone_lon = telemetry["lat"], telemetry["lon"]
        position_source = "lora"
    else:
        loc = location_worker.status()
        if not loc["available"]:
            return jsonify({"available": False, "reason": "no drone position yet (no LoRa telemetry heard, no local GPS fix)", "members": members})
        drone_lat, drone_lon = loc["lat"], loc["lon"]
        position_source = "local"

    # Always read LocationWorker's own accuracy_m for context, regardless
    # of which path answered above: it describes the WiFi-geolocation
    # method itself (the only source either path's fix ultimately comes
    # from on today's single-Pi bench setup -- see LocationWorker.run()),
    # not something that literally rode over the wire. Shown so a jumpy
    # distance reading is legible as sensor noise, not treated as exact.
    survivor_accuracy_m = location_worker.status().get("accuracy_m")

    if not members:
        return jsonify({"available": False, "reason": "no rescue-team phone reporting location", "members": members})

    for m in members:
        m["distance_m"] = round(_haversine_m(drone_lat, drone_lon, m["lat"], m["lon"]), 1)
    members.sort(key=lambda m: m["distance_m"])
    nearest = members[0]

    # Both points projected into the same simulated map frame the web
    # dashboard's Mission Map already draws the drone marker in (see
    # /api/mission/position) -- so a path line between them lines up with
    # where their markers actually sit, not a second, disagreeing
    # coordinate system. Clamped the same way for the same reason: an
    # off-map real position stays a marker at the edge, not one that
    # silently vanishes or repositions what "the map" means.
    survivor_x_m, survivor_y_m = _latlon_to_meters(drone_lat, drone_lon)
    responder_x_m, responder_y_m = _latlon_to_meters(nearest["lat"], nearest["lon"])

    def _clamp(v: float) -> float:
        return round(max(0.0, min(MISSION_AREA_M, v)), 1)

    return jsonify({
        "available": True,
        "distance_m": nearest["distance_m"],
        "member_name": nearest["name"],
        "member_id": nearest["member_id"],
        "seconds_since_update": nearest["seconds_since_update"],
        "survivor_accuracy_m": survivor_accuracy_m,
        "position_source": position_source,
        "survivor_lat": drone_lat,
        "survivor_lon": drone_lon,
        "survivor_x_m": _clamp(survivor_x_m),
        "survivor_y_m": _clamp(survivor_y_m),
        "responder_lat": nearest["lat"],
        "responder_lon": nearest["lon"],
        "responder_x_m": _clamp(responder_x_m),
        "responder_y_m": _clamp(responder_y_m),
        "members": members,
    })


@app.route("/api/vo/status")
def api_vo_status():
    """Visual-odometry tracking health. See VisualOdometryWorker's module
    comment for exactly what this is (frame-to-frame monocular odometry,
    not full SLAM: no loop closure, no metric scale, drifts over time)."""
    return jsonify(vo_worker.status())


@app.route("/api/vo/trajectory")
def api_vo_trajectory():
    """Recent camera-derived trajectory points, in relative meters from
    wherever this process started -- NOT GPS-referenced and NOT the same
    thing as /api/mission/position's simulated lawnmower track. See
    VisualOdometryWorker's module comment before treating this as a
    metric map."""
    limit = request.args.get("limit", default=500, type=int)
    return jsonify({
        "points": vo_worker.recent_trajectory(limit=limit),
        **vo_worker.status(),
    })


@app.route("/api/triage")
def api_triage():
    """Survivor detections ranked by explainable priority -- see
    triage_priority(). Highest-priority first; each entry carries the
    alert's own fields plus `priority` (score + human-readable reasons),
    so the dashboard never has to re-derive the ranking client-side."""
    alerts = alert_store.list_since()
    ranked = []
    for a in alerts:
        entry = asdict(a)
        entry["priority"] = triage_priority(a)
        ranked.append(entry)
    ranked.sort(key=lambda e: e["priority"]["score"], reverse=True)
    return jsonify(ranked)


@app.route("/api/mission/report")
def api_mission_report():
    """Auto-generated plain-English mission briefing -- see
    generate_mission_report(). Returned as both a ready-to-read `text`
    block and the individual numbers it was built from, so the dashboard
    can show either the prose or its own styled version of the same
    facts without re-querying three other endpoints."""
    alerts = alert_store.list_since()
    hazards = hazard_store.list_since()
    active_hazards = [h for h in hazards if time.time() - h.created_at < HAZARD_CORRELATION_WINDOW_S]
    return jsonify({
        "text": generate_mission_report(),
        "generated_at": time.time(),
        "coverage_pct": simulated_coverage_pct(),
        "survivors_total": len(alerts),
        "survivors_verified": sum(1 for a in alerts if a.thermal_verified),
        "survivors_critical": sum(1 for a in alerts if a.near_hazard_type),
        "active_hazards": len(active_hazards),
    })


@app.route("/ml")
def ml_dashboard():
    """Vision/AI dashboard: camera feed, model, detections."""
    return render_template("ml_dashboard.html")


@app.route("/gcs")
def gcs_dashboard():
    """Ground-station dashboard: LoRa radio link and mesh-relayed alerts."""
    return render_template("gcs_dashboard.html")


@app.route("/api/health")
def api_health():
    """Cheap reachability probe. The phone app uses this to decide whether
    it's talking to this Pi over the internet/cloud path or the local
    network/hotspot path -- see the app's ConnectionManager."""
    return jsonify({"status": "ok", "time": time.time(), "hostname": os.uname().nodename})


def main():
    # Start background detection thread
    worker_thread = threading.Thread(target=worker.run, daemon=True)
    worker_thread.start()

    hazard_thread = threading.Thread(target=hazard_worker.run, daemon=True)
    hazard_thread.start()

    # Replaced by rescue-team/drone map; do not spend CPU on unused ORB tracking.
    if os.environ.get("ARGUS_ENABLE_ODOMETRY") == "1":
        threading.Thread(target=vo_worker.run, daemon=True).start()
    threading.Thread(target=sample_system_telemetry, daemon=True).start()

    location_thread = threading.Thread(target=location_worker.run, daemon=True)
    location_thread.start()

    if drone_link is not None:
        drone_link.start()

    port = 5000
    host = "0.0.0.0"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        network_ip = s.getsockname()[0]
        s.close()
    except Exception:
        network_ip = "<this device's LAN IP>"
    print(f"\n=======================================================")
    print(f" Drone AI Surveillance Dashboard Live!")
    print(f" Local URL:    http://localhost:{port}")
    print(f" Network URL:  http://{network_ip}:{port}")
    if drone_link is not None:
        print(f" LoRa uplink:  direct-SPI RA-02 (SPI{drone_link.spi_bus}.{drone_link.spi_device})")
    print(f" Hazard model: {'loaded' if hazard_worker.model is not None else 'NOT LOADED'}")
    print(f" Visual odometry: {'calibrated' if vo_worker.calibrated else 'UNCALIBRATED (approximate intrinsics)'}")
    print(f"=======================================================\n")

    try:
        app.run(host=host, port=port, threaded=True, debug=False)
    except KeyboardInterrupt:
        print("\n[*] Shutting down dashboard...")
    finally:
        worker.stop()
        hazard_worker.stop()
        vo_worker.stop()
        if drone_link is not None:
            drone_link.stop()


if __name__ == "__main__":
    main()
