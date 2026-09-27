"""Drone Pi -> drone ESP32 -> LoRa uplink.

Wraps the serial link to firmware/esp32_drone. The ESP32 is a dumb pipe --
this module builds the complete, addressed SAR packet (reusing the exact
tested encoder in sar_mesh.packet) and hands the ESP32 finished bytes to
transmit verbatim.

Designed to be safe to construct and use even when no ESP32 is actually
plugged in: this Pi has been running its camera/detection pipeline all
session without this hardware attached, and adding LoRa integration must
not turn a missing/unplugged radio into a crashed dashboard. Every failure
mode here is best-effort -- log once, keep the detection loop running.

Deploy: copy this file, lora_bridge.py, and the whole sar/ package
(config.py, packet.py, serial_link.py, __init__.py) into Drone-model/ on
the Pi, preserving the sar/ subdirectory -- these import `from sar...`
exactly as the rest of this project does, no renaming. See
docs/ESP32_LORA_INTEGRATION.md.
"""

from __future__ import annotations

import glob
import logging
import queue
import threading
import time

from sar import config
from sar.packet import HumanDetected, MsgType, Packet, SequenceGenerator
from sar.serial_link import frame

log = logging.getLogger("lora_uplink")

DEFAULT_BAUD = 115200
CANDIDATE_PORTS = ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/ttyACM1")
QUEUE_MAXSIZE = 32
RECONNECT_BACKOFF_S = 3.0


class DroneLoRaLink:
    """Call .start() once at app startup, .send_detection(...) per alert.

    Runs its own writer thread with a bounded queue so a slow or wedged
    serial port can never block the caller (the camera/detection loop) --
    if the queue is full, the oldest-pending send is dropped in favour of
    the newest detection, on the reasoning that a rescue team needs the
    survivor's *current* alert more than a stale duplicate of an older one.
    """

    def __init__(
        self,
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        src_address: int = config.ADDR_DRONE_01,
        dst_address: int = config.ADDR_GCS,
    ) -> None:
        self.port = port
        self.baud = baud
        self.src_address = src_address
        self.dst_address = dst_address

        self._seq = SequenceGenerator()
        self._queue: queue.Queue[Packet] = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._thread: threading.Thread | None = None
        self._running = False
        self._serial = None
        self._warned_no_port = False

        self.stats = {"queued": 0, "sent": 0, "dropped_queue_full": 0, "serial_errors": 0}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="lora-uplink", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass

    def send_detection(
        self,
        lat: float | None,
        lon: float | None,
        confidence: float,
        person_count: int = 1,
        alt_m: int = 0,
        ttl: int = 5,
    ) -> None:
        """Build and enqueue a HUMAN_DETECTED packet. Non-blocking, always
        safe to call -- including when no ESP32 is attached at all, in
        which case this is a no-op past the first warning."""
        pkt = Packet(
            msg_type=MsgType.HUMAN_DETECTED,
            src=self.src_address,
            dst=self.dst_address,
            seq=self._seq.next(),
            payload=HumanDetected(
                lat=lat, lon=lon, alt_m=alt_m, confidence=confidence, person_count=person_count
            ),
            ttl=ttl,
            hop=0,
            last_hop=self.src_address,
        )
        try:
            self._queue.put_nowait(pkt)
            self.stats["queued"] += 1
        except queue.Full:
            try:
                self._queue.get_nowait()  # drop the oldest pending send
            except queue.Empty:
                pass
            self._queue.put_nowait(pkt)
            self.stats["dropped_queue_full"] += 1
            log.warning("uplink queue full -- dropped oldest pending detection")

    # ---- internals --------------------------------------------------------

    def _find_port(self) -> str | None:
        if self.port:
            return self.port
        for pattern in CANDIDATE_PORTS:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        return None

    def _connect(self):
        import serial  # pyserial; imported lazily so this module loads fine without it too

        port = self._find_port()
        if port is None:
            if not self._warned_no_port:
                log.warning(
                    "no drone ESP32 found on %s -- detections will be logged but not "
                    "transmitted over LoRa until one is connected",
                    ", ".join(CANDIDATE_PORTS),
                )
                self._warned_no_port = True
            return None
        try:
            ser = serial.Serial(port, self.baud, timeout=1.0)
            log.info("drone uplink connected on %s @ %d baud", port, self.baud)
            self._warned_no_port = False
            return ser
        except Exception as e:
            log.warning("could not open %s: %s", port, e)
            return None

    def _run(self) -> None:
        while self._running:
            if self._serial is None:
                self._serial = self._connect()
                if self._serial is None:
                    time.sleep(RECONNECT_BACKOFF_S)
                    continue

            try:
                pkt = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self._serial.write(frame(pkt.encode()))
                self._serial.flush()
                self.stats["sent"] += 1
                log.info("uplinked %s to drone ESP32 (%d bytes)", pkt.packet_id, pkt.size_bytes)
            except Exception as e:
                self.stats["serial_errors"] += 1
                log.warning("serial write failed (%s) -- will reconnect", e)
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                # Put the packet back so a real detection is not silently
                # lost to a transient disconnect, unless that would exceed
                # the queue bound -- then it is genuinely stale by the time
                # we reconnect, and the newer entries behind it matter more.
                try:
                    self._queue.put_nowait(pkt)
                except queue.Full:
                    pass
