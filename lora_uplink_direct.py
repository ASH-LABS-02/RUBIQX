"""Drone Pi -> RA-02 direct-SPI LoRa uplink -- no ESP32 on the drone side.

lora_uplink.py's DroneLoRaLink assumes the drone's Pi talks to an ESP32
dumb-pipe over USB/UART, which then owns the RA-02. This Pi's actual,
hardware-verified wiring is different (see tools/checkradio.py): the RA-02
is wired straight to the Pi's own SPI bus (sar/radio/sx127x.py), no ESP32
involved at all on this side. This module is the uplink for that
configuration, with the exact same send_detection(...) interface as
DroneLoRaLink so app.py's call site doesn't change regardless of which
one it's holding.

Same non-blocking, never-crash-the-detection-loop philosophy as
DroneLoRaLink: constructing and calling this is always safe, including
with no RA-02 attached at all, in which case it logs one warning and is a
silent no-op from then on.

Deploy: copy this file and the whole sar/ package (config.py, packet.py,
mesh.py, radio/) into Drone-model/ on the Pi, alongside lora_uplink.py --
see docs/ESP32_LORA_INTEGRATION.md for the general deploy layout.
"""

from __future__ import annotations

import queue
import threading

from sar import config
from sar.mesh import MeshNode
from sar.packet import HumanDetected, MissionState, MsgType, Telemetry

QUEUE_MAXSIZE = 32
TELEMETRY_QUEUE_MAXSIZE = 4  # only the latest position is ever worth sending


class DirectRadioUplink:
    """Call .start() once at app startup, .send_detection(...) per alert."""

    def __init__(
        self,
        profile_name: str | None = None,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 25,
        dio0_pin: int = 24,
        spi_speed_hz: int = 500_000,
        src_address: int = config.ADDR_DRONE_01,
        dst_address: int = config.ADDR_GCS,
    ) -> None:
        self.profile = config.PROFILES[profile_name or config.DEFAULT_PROFILE.name]
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.reset_pin = reset_pin
        self.dio0_pin = dio0_pin
        self.spi_speed_hz = spi_speed_hz
        self.src_address = src_address
        self.dst_address = dst_address

        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._telemetry_queue: queue.Queue = queue.Queue(maxsize=TELEMETRY_QUEUE_MAXSIZE)
        self._thread: threading.Thread | None = None
        self._running = False
        self._node: MeshNode | None = None
        self._ready = False

        self.stats = {
            "queued": 0, "sent": 0, "dropped_queue_full": 0, "radio_errors": 0,
            "telemetry_sent": 0,
        }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="lora-uplink-direct", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._node is not None:
            self._node.stop()

    def send_detection(
        self,
        lat: float | None,
        lon: float | None,
        confidence: float,
        person_count: int = 1,
        alt_m: int = 0,
        ttl: int = 5,
    ) -> None:
        """Non-blocking, always safe to call -- including before the radio
        thread has finished bringing the chip up, or if it never found a
        radio at all, in which case this is a silent no-op."""
        if not self._ready:
            return
        item = (lat, lon, confidence, person_count, alt_m, ttl)
        try:
            self._queue.put_nowait(item)
            self.stats["queued"] += 1
        except queue.Full:
            try:
                self._queue.get_nowait()  # drop the oldest pending send
            except queue.Empty:
                pass
            self._queue.put_nowait(item)
            self.stats["dropped_queue_full"] += 1
            print("[!] direct-SPI LoRa uplink queue full -- dropped oldest pending detection")

    def send_telemetry(
        self,
        lat: float,
        lon: float,
        alt_m: int = 0,
        battery_pct: int = 0,
        state: MissionState = MissionState.SEARCH,
        heading_deg: int = 0,
        ttl: int = 5,
    ) -> None:
        """Non-blocking periodic position beacon -- the drone's own real
        fix (see app.py's LocationWorker), sent over the actual LoRa link
        so a GCS Pi that is genuinely a separate machine (not sharing this
        process's memory, unlike the current single-Pi bench setup -- see
        lora_bridge.py's module comment) still learns where the drone is.
        Not life-safety traffic like a detection, so no `priority=True`
        and a tiny 4-deep queue: a stale queued position is worthless once
        a newer one exists, unlike a detection which must never be
        dropped for being old.
        """
        if not self._ready:
            return
        item = (lat, lon, alt_m, battery_pct, state, heading_deg, ttl)
        try:
            self._telemetry_queue.put_nowait(item)
        except queue.Full:
            try:
                self._telemetry_queue.get_nowait()
            except queue.Empty:
                pass
            self._telemetry_queue.put_nowait(item)

    # ---- internals --------------------------------------------------------

    def _run(self) -> None:
        from sar.radio.sx127x import SX127xRadio

        try:
            radio = SX127xRadio(
                profile=self.profile,
                spi_bus=self.spi_bus,
                spi_device=self.spi_device,
                reset_pin=self.reset_pin,
                dio0_pin=self.dio0_pin,
                spi_speed_hz=self.spi_speed_hz,
            )
            radio.start()
            self._node = MeshNode(address=self.src_address, radio=radio, forward=False)
            self._node.start()
            self._ready = True
            print(
                f"[+] direct-SPI LoRa uplink ready "
                f"({self.profile.name} profile, SPI{self.spi_bus}.{self.spi_device})"
            )
        except Exception as e:
            print(
                f"[!] no RA-02 found on SPI{self.spi_bus}.{self.spi_device} ({e}) -- "
                f"detections will be logged but not transmitted over LoRa"
            )
            return

        while self._running:
            try:
                lat, lon, confidence, person_count, alt_m, ttl = self._queue.get(timeout=0.5)
            except queue.Empty:
                pass
            else:
                try:
                    # priority=True: a human detection is exactly the life-safety
                    # traffic the mesh's duty-cycle exemption exists for -- see
                    # sar/mesh.py's MeshNode.send() and the project README.
                    pkt = self._node.send(
                        MsgType.HUMAN_DETECTED,
                        HumanDetected(
                            lat=lat, lon=lon, alt_m=alt_m,
                            confidence=confidence, person_count=person_count,
                        ),
                        dst=self.dst_address,
                        ttl=ttl,
                        priority=True,
                    )
                    self.stats["sent"] += 1
                    print(f"[+] uplinked {pkt.packet_id} over direct-SPI LoRa ({pkt.size_bytes} bytes)")
                except Exception as e:
                    self.stats["radio_errors"] += 1
                    print(f"[!] direct-SPI LoRa send failed: {e}")

            # Checked every pass through this loop (at least every ~0.5s,
            # bounded above by the detection .get() timeout), independent
            # of whether a detection was also sent this tick -- callers
            # only queue a new position every LOCATION_UPDATE_INTERVAL_S
            # (tens of seconds), so this just needs to notice it eventually,
            # not immediately.
            try:
                t_lat, t_lon, t_alt, t_batt, t_state, t_heading, t_ttl = self._telemetry_queue.get_nowait()
            except queue.Empty:
                continue
            try:
                pkt = self._node.send(
                    MsgType.TELEMETRY,
                    Telemetry(
                        lat=t_lat, lon=t_lon, alt_m=t_alt,
                        battery_pct=t_batt, state=t_state, heading_deg=t_heading,
                    ),
                    dst=self.dst_address,
                    ttl=t_ttl,
                    priority=False,
                )
                self.stats["telemetry_sent"] += 1
                print(f"[+] uplinked {pkt.packet_id} TELEMETRY over direct-SPI LoRa ({pkt.size_bytes} bytes)")
            except Exception as e:
                self.stats["radio_errors"] += 1
                print(f"[!] direct-SPI LoRa telemetry send failed: {e}")
