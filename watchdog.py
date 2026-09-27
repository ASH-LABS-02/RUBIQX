#!/usr/bin/env python3
"""Process-level watchdog for the detection dashboard.

The camera capture call (Picamera2.capture_array()) has been observed to
hang completely on this OV5647 sensor -- no exception raised, no CPU
used, DetectionWorker.run()'s loop simply stops advancing. That is a
native/kernel-level block a thread *inside* the same process can't
reliably interrupt: Python threads can't be force-killed, and calling
stop()/close() on the camera from another thread while capture_array()
is still mid-call is unsafe (undefined behaviour against a native
library, and no guarantee it even unblocks the call). The only reliable
recovery is killing the whole process from the outside and starting it
fresh -- that always works, even against a truly hung native call,
because the OS can always tear down a process regardless of what any
thread inside it is blocked on.

Run this instead of running app.py directly:
    .venv/bin/python -u watchdog.py

It launches app.py as a child process, polls /api/stats every
POLL_INTERVAL_S, and restarts the child if total_frames hasn't advanced
in STALL_TIMEOUT_S. Also restarts on an unexpected exit (crash) or if
/api/stats stays unreachable past the startup grace period. Runs forever
-- there is no "give up" state, because the alternative is a frozen feed
nobody notices until someone checks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SECRETS_FILE = Path.home() / ".sar_secrets.env"
"""Simple KEY=VALUE file, one per line, outside Drone-model/ so a secret
never ends up in this repo if it's ever git-init'd. Loaded into this
process's own environment before app.py is spawned, which is what makes
it visible there too -- subprocess.Popen below passes no explicit `env=`,
so the child inherits whatever this process already has. This is the one
and only place a credential like GOOGLE_GEOLOCATION_API_KEY needs to be
set for the whole stack to pick it up; nothing should read SECRETS_FILE
directly."""


def load_secrets() -> None:
    if not SECRETS_FILE.exists():
        return
    for line in SECRETS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


STATS_URL = "http://localhost:5000/api/stats"
POLL_INTERVAL_S = 5.0
STALL_TIMEOUT_S = 15.0
STARTUP_GRACE_S = 25.0  # camera init + model load takes a few seconds


def get_total_frames() -> int | None:
    try:
        with urllib.request.urlopen(STATS_URL, timeout=3.0) as r:
            return json.load(r)["total_frames"]
    except Exception:
        return None


def run_once() -> None:
    print("[watchdog] starting app.py", flush=True)
    proc = subprocess.Popen([sys.executable, "-u", "app.py"])
    start_time = time.monotonic()
    last_frames: int | None = None
    last_change = time.monotonic()

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)

            if proc.poll() is not None:
                print(f"[watchdog] app.py exited on its own (code {proc.returncode}) -- restarting", flush=True)
                return

            frames = get_total_frames()
            now = time.monotonic()

            if frames is None:
                if now - start_time > STARTUP_GRACE_S:
                    print("[watchdog] /api/stats still unreachable past startup grace -- restarting", flush=True)
                    break
                continue

            if frames != last_frames:
                last_frames = frames
                last_change = now
                continue

            if now - last_change > STALL_TIMEOUT_S:
                print(f"[watchdog] total_frames stuck at {frames} for {STALL_TIMEOUT_S:.0f}s -- restarting", flush=True)
                break
    finally:
        if proc.poll() is None:
            print("[watchdog] killing stalled app.py", flush=True)
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print("[watchdog] app.py did not die within 10s of SIGKILL -- this should not happen", flush=True)


def main() -> None:
    load_secrets()
    while True:
        run_once()


if __name__ == "__main__":
    main()
