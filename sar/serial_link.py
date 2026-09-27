"""Serial-framing protocol between a Raspberry Pi and its attached ESP32
LoRa radio (firmware/esp32_drone or firmware/esp32_gcs).

Canonical source -- vendored onto the GCS/drone Pi as part of the
sar_mesh package (see docs/ESP32_LORA_INTEGRATION.md). Change it here,
re-copy, same discipline as sar/packet.py and firmware/esp32_relay.

Frame format, identical in both directions and matching the ESP32 sketches
byte-for-byte::

    SYNC0(0xAA) SYNC1(0x55) LEN(u8) <LEN bytes payload> CHECKSUM(u8)

CHECKSUM is the XOR of the LEN byte and every payload byte. There is no
length-of-length or escaping -- LEN is a plain byte, so a frame is capped
at 255 bytes of payload, comfortably above a full SAR packet (21 bytes for
a detection, up to ~255 total).

This module provides the framing only. What goes inside the payload is
different in each direction:

    drone Pi -> drone ESP32   the payload IS a complete sar_mesh.packet
                               Packet.encode() -- the ESP32 transmits it
                               verbatim, see DroneLoRaLink below.

    GCS ESP32 -> GCS Pi        the payload is 2 bytes (RSSI, SNR*4) followed
                               by the raw packet as heard over the air --
                               see lora_bridge.py, which is the consumer.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger("lora_serial")

SYNC0 = 0xAA
SYNC1 = 0x55

MAX_PAYLOAD = 255
# A real frame is at most 3 + 255 + 1 = 259 bytes. If we lock onto a sync
# sequence that turns out to be inside noise or mid-payload garbage rather
# than a genuine frame start, and the LEN byte we read as a result is
# nonsense, we must not wait forever for a frame that will never complete --
# that would silently swallow every real frame that arrives afterward too.
# This bound is how long we'll wait before giving up on a sync match and
# resuming the search one byte later.
_STALL_BYTES = 600


def frame(payload: bytes) -> bytes:
    """Wrap a payload for transmission. Raises ValueError over 255 bytes --
    that is a caller bug (a SAR packet never gets this large), not
    something to silently truncate."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too long for one frame: {len(payload)} > {MAX_PAYLOAD}")
    checksum = len(payload)
    for b in payload:
        checksum ^= b
    return bytes([SYNC0, SYNC1, len(payload)]) + payload + bytes([checksum & 0xFF])


class FrameReader:
    """Incremental parser: feed it bytes as they arrive from a serial port,
    get back zero or more complete, checksum-valid payloads.

    Mirrors the ESP32 firmware's own tiny state machine exactly -- same
    resync-on-any-failure behaviour, so a dropped byte, electrical noise, or
    either side resetting mid-frame never wedges the link. Deliberately does
    NOT raise on bad data; a serial link to a physical radio is expected to
    occasionally see garbage, and the correct response is to resync, not to
    crash the process reading it.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        out: list[bytes] = []

        while True:
            sync_at = self._buf.find(bytes([SYNC0, SYNC1]))
            if sync_at == -1:
                # No sync in the buffer at all. Keep a trailing lone SYNC0
                # in case the SYNC1 is the very next byte to arrive; drop
                # everything else, there is nothing usable in it.
                self._buf = self._buf[-1:] if self._buf[-1:] == bytes([SYNC0]) else bytearray()
                break

            del self._buf[:sync_at]  # drop any garbage before the sync

            if len(self._buf) < 4:
                break  # have SYNC0 SYNC1 but not even a LEN + CHECKSUM yet

            length = self._buf[2]
            total = 3 + length + 1
            if len(self._buf) < total:
                if len(self._buf) > _STALL_BYTES:
                    # This sync match's LEN implies a frame that still
                    # hasn't completed after an implausible number of
                    # bytes -- almost certainly a spurious match inside
                    # noise, not a real frame. Abandon it and resume
                    # scanning one byte later rather than stalling forever.
                    log.debug("stalled frame candidate (len=%d), resyncing", length)
                    del self._buf[:1]
                    continue
                break  # legitimately still waiting on more bytes

            payload = bytes(self._buf[3 : 3 + length])
            checksum = self._buf[3 + length]
            expected = length
            for b in payload:
                expected ^= b

            del self._buf[:total]

            if checksum == expected:
                out.append(payload)
            else:
                log.debug("checksum mismatch on %d-byte frame, dropping", length)
                # fall through and keep scanning what's left in the buffer

        return out
