#!/usr/bin/env python3
"""GCS ESP32 -> Ground Station Pi -> alerts gateway bridge.

Run this alongside app.py on the Ground Station Pi:

    python3 lora_bridge.py
    python3 lora_bridge.py --port /dev/ttyUSB0 --gateway-url http://localhost:5000

Reads framed reports from firmware/esp32_gcs (RSSI, SNR, and the raw SAR
packet exactly as heard over the air), decodes them with the same tested
code as the rest of this project (sar_mesh.packet), and POSTs each new
detection to the gateway's /api/alerts/ingest -- the same endpoint and
schema a LoRa-relayed alert was always meant to use (see the comment block
above that route in app.py). From the mobile app's point of view, an alert
that arrived this way is indistinguishable from one raised by this Pi's
own camera, except for its `source` and `route` fields.

--gateway-url defaults to localhost because right now the drone's camera
Pi and the Ground Station Pi are frequently the same physical machine on
the bench (two ESP32s, two USB ports, one Pi) -- see docs/
ESP32_LORA_INTEGRATION.md. Once the drone is flying with its own Pi, point
this at the real Ground Station's address instead; nothing else changes.

Deploy: copy this file, lora_uplink.py, and the whole sar/ package
(config.py, packet.py, serial_link.py, __init__.py) into Drone-model/ on
the Pi, preserving the sar/ subdirectory. Also needs `pip install pyserial
requests` in Drone-model's venv if either is not already present.
"""

from __future__ import annotations

import argparse
import logging
import struct
import sys
import time
from dataclasses import dataclass

import requests

from sar import config
from sar.packet import MsgType, Packet, PacketError
from sar.serial_link import FrameReader

log = logging.getLogger("lora_bridge")

DEFAULT_BAUD = 115200
DEDUP_RETENTION_S = 180.0
DEDUP_MAX_ENTRIES = 512
RECONNECT_BACKOFF_S = 3.0


class SeenCache:
    """Bounded, time-expiring (source, sequence) dedup -- a flood mesh can
    deliver the same detection by more than one path, and the mobile app
    must not show the same survivor twice. Mirrors sar/mesh.py's SeenCache;
    reimplemented small and standalone here rather than vendoring the full
    mesh module, which carries a lot this receive-only bridge doesn't need.
    """

    def __init__(self, retention_s: float = DEDUP_RETENTION_S, max_entries: int = DEDUP_MAX_ENTRIES) -> None:
        self.retention_s = retention_s
        self.max_entries = max_entries
        self._seen: dict[tuple[int, int], float] = {}

    def check_and_add(self, uid: tuple[int, int]) -> bool:
        now = time.monotonic()
        cutoff = now - self.retention_s
        for key in [k for k, t in self._seen.items() if t < cutoff]:
            del self._seen[key]

        if uid in self._seen:
            return False
        self._seen[uid] = now
        while len(self._seen) > self.max_entries:
            oldest = min(self._seen, key=self._seen.get)
            del self._seen[oldest]
        return True


@dataclass
class Report:
    rssi_dbm: int
    snr_db: float
    packet: Packet


def parse_report(payload: bytes) -> Report | None:
    """Unpacks one esp32_gcs frame payload: RSSI(int8) SNR*4(int8) <packet>."""
    if len(payload) < 2:
        log.warning("report too short to contain RSSI/SNR (%d bytes)", len(payload))
        return None
    rssi, snr_x4 = struct.unpack("<bb", payload[:2])
    raw_packet = payload[2:]
    try:
        pkt = Packet.decode(raw_packet)
    except PacketError as e:
        log.warning("dropped unparseable packet (%d bytes): %s", len(raw_packet), e)
        return None
    return Report(rssi_dbm=rssi, snr_db=snr_x4 / 4.0, packet=pkt)


def ingest(gateway_url: str, report: Report) -> bool:
    payload = report.packet.payload
    body = {
        "confidence": round(payload.confidence, 1),
        "person_count": payload.person_count,
        "source": "lora-mesh",
        "lat": payload.lat,
        "lon": payload.lon,
        "route": report.packet.route,
        "hops": report.packet.hop,
        "rssi_dbm": report.rssi_dbm,
    }
    try:
        res = requests.post(f"{gateway_url}/api/alerts/ingest", json=body, timeout=5.0)
        if res.status_code == 201:
            return True
        log.warning("gateway rejected ingest (%d): %s", res.status_code, res.text[:200])
        return False
    except requests.RequestException as e:
        log.warning("could not reach gateway at %s: %s", gateway_url, e)
        return False


def report_telemetry(gateway_url: str, report: Report) -> bool:
    """Forwards a decoded TELEMETRY frame's position to the gateway's
    /api/mesh/telemetry -- see sar/packet.py's Telemetry payload and
    lora_uplink_direct.py's send_telemetry(). This is what lets
    /api/team/nearest use the drone's position as it actually arrived over
    the air, instead of reading LocationWorker's in-process state directly
    (that shortcut only happens to work because the drone and GCS are the
    same bench Pi right now -- see this module's docstring; a real,
    physically separate GCS has no such shortcut available)."""
    payload = report.packet.payload
    body = {"lat": payload.lat, "lon": payload.lon, "alt_m": payload.alt_m}
    try:
        res = requests.post(f"{gateway_url}/api/mesh/telemetry", json=body, timeout=5.0)
        return res.status_code == 200
    except requests.RequestException as e:
        log.warning("could not reach gateway for telemetry at %s: %s", gateway_url, e)
        return False


def heartbeat(gateway_url: str, port: str, report: Report, total_frames: int, is_detection: bool) -> None:
    """Tells the gateway's /api/mesh/status this link is alive -- called on
    every decoded frame, not just ones that become alerts (see the comment
    on that route in app.py), so the ground-station dashboard can show a
    live channel between detections instead of only updating whenever a
    HUMAN_DETECTED packet happens to arrive. Best-effort: a missed
    heartbeat isn't worth losing the frame over, so failures just log and
    move on, same as a rejected ingest() above.

    Includes `hop` so the dashboard can tell whether NODE-01 (the ESP32
    relay) has actually been heard, independent of whether THIS specific
    copy is the one that ends up stored as the alert. A relayed copy is
    deliberately delayed by jitter (see firmware/esp32_relay), so on a
    bench where direct and relayed paths both reach the GCS, the zero-
    delay direct copy almost always wins AlertStore's (src,seq) dedup and
    is what ingest() stores -- the relayed copy still arrives moments
    later, gets logged as a duplicate, and never touches route/hops on any
    stored alert. Without reporting hop here on every heard frame (not just
    ingested ones), the dashboard would show "no relay activity" even while
    the relay is demonstrably working, just never winning that race.
    """
    body = {
        "port": port,
        "rssi_dbm": report.rssi_dbm,
        "snr_db": report.snr_db,
        "total_frames": total_frames,
        "is_detection": is_detection,
        "hop": report.packet.hop,
    }
    try:
        requests.post(f"{gateway_url}/api/mesh/heartbeat", json=body, timeout=5.0)
    except requests.RequestException as e:
        log.warning("could not reach gateway for heartbeat at %s: %s", gateway_url, e)


def run(port: str, baud: int, gateway_url: str) -> None:
    import serial

    seen = SeenCache()
    reader = FrameReader()
    stats = {"frames": 0, "decoded": 0, "duplicates": 0, "ingested": 0, "ignored_type": 0}

    while True:
        try:
            ser = serial.Serial(port, baud, timeout=1.0)
            log.info("GCS downlink connected on %s @ %d baud -> %s", port, baud, gateway_url)
        except Exception as e:
            log.warning("could not open %s: %s -- retrying in %.0fs", port, e, RECONNECT_BACKOFF_S)
            time.sleep(RECONNECT_BACKOFF_S)
            continue

        try:
            while True:
                chunk = ser.read(256)
                if not chunk:
                    continue
                for payload in reader.feed(chunk):
                    stats["frames"] += 1
                    report = parse_report(payload)
                    if report is None:
                        continue
                    stats["decoded"] += 1

                    pkt = report.packet
                    log.info(
                        "heard %s  %s->%s  %s  hop=%d  %ddBm  %.1fdB SNR",
                        pkt.packet_id, config.node_name(pkt.src), config.node_name(pkt.dst),
                        pkt.msg_type.name, pkt.hop, report.rssi_dbm, report.snr_db,
                    )
                    heartbeat(gateway_url, port, report, stats["decoded"], pkt.msg_type == MsgType.HUMAN_DETECTED)

                    if pkt.msg_type == MsgType.TELEMETRY:
                        if pkt.dst in (config.ADDR_GCS, config.ADDR_BROADCAST):
                            if report_telemetry(gateway_url, report):
                                stats["telemetry"] = stats.get("telemetry", 0) + 1
                        continue

                    if pkt.msg_type != MsgType.HUMAN_DETECTED:
                        stats["ignored_type"] += 1
                        continue
                    if pkt.dst not in (config.ADDR_GCS, config.ADDR_BROADCAST):
                        continue  # overheard, addressed elsewhere -- not ours to ingest

                    if not seen.check_and_add(pkt.uid):
                        stats["duplicates"] += 1
                        log.info("  duplicate of an already-ingested %s, skipped", pkt.packet_id)
                        continue

                    if ingest(gateway_url, report):
                        stats["ingested"] += 1
                        log.info("  -> ingested (%s)", stats)
        except serial.SerialException as e:
            log.warning("serial link lost (%s) -- reconnecting", e)
        finally:
            try:
                ser.close()
            except Exception:
                pass
        time.sleep(RECONNECT_BACKOFF_S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="/dev/ttyUSB0", help="serial port for the GCS ESP32")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--gateway-url", default="http://localhost:5000",
                        help="Drone-model/app.py base URL (default: %(default)s)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        run(args.port, args.baud, args.gateway_url)
    except KeyboardInterrupt:
        print("\nstopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
