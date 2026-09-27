#!/usr/bin/env bash
# Startup script for Drone AI Dashboard on Raspberry Pi 5.
# Runs through watchdog.py, not app.py directly -- the camera has been
# observed to hang (no crash, no error, just stops) and watchdog.py is
# what detects and recovers from that. See watchdog.py's own docstring.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if pgrep -f 'python.*watchdog.py' > /dev/null; then
    echo '[!] Dashboard (watchdog) is already running on port 5000.'
    echo "[*] Access it at: http://localhost:5000 or http://$(hostname -I | awk '{print $1}'):5000"
    exit 0
fi

echo '[*] Starting Drone AI Dashboard (with camera watchdog) on Raspberry Pi 5...'
echo '[*] Local URL:   http://localhost:5000'
echo "[*] Network URL: http://$(hostname -I | awk '{print $1}'):5000"

.venv/bin/python -u watchdog.py
