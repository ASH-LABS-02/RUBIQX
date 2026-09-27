"""On-air packet format for the SAR mesh.

Every byte here costs airtime, so the wire format is packed binary.  A
human-detection packet is 21 bytes total, which is 371 ms on air at SF10 /
125 kHz.  The same struct is implemented byte-for-byte in the ESP32 relay
firmware (``firmware/esp32_relay``) -- if you change anything in this file,
change it there too.

Header layout (8 bytes)::

    byte 0   VERSION (4 bits) | MSG_TYPE (4 bits)
    byte 1   SRC       originating node, never rewritten
    byte 2   DST       final destination, never rewritten
    byte 3   TTL (4 bits) | HOP (4 bits)
    byte 4   SEQ low    ) (SRC, SEQ) is the globally unique packet id
    byte 5   SEQ high   )
    byte 6   LAST_HOP  node that actually transmitted this copy, rewritten
                       at every relay so the sink learns the delivery path
    byte 7   PAYLOAD LENGTH

There is no software checksum: the SX127x computes and verifies a hardware
CRC over the whole payload, and a packet that fails it is never handed up.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar

from . import config

PROTOCOL_VERSION = 1
HEADER_SIZE = 8
MAX_PAYLOAD = 247  # SX127x FIFO is 255 bytes
MAX_TTL = 15
MAX_HOP = 15


class PacketError(ValueError):
    """Raised when bytes off the air cannot be parsed as a valid packet."""


class MsgType(IntEnum):
    HEARTBEAT = 0
    TELEMETRY = 1
    HUMAN_DETECTED = 2
    ACK = 3
    COMMAND = 4
    MISSION_EVENT = 5
    OBSTACLE_EVENT = 6
    NODE_STATUS = 7


class MissionState(IntEnum):
    IDLE = 0
    PREFLIGHT = 1
    TAKEOFF = 2
    SEARCH = 3
    DETECTED = 4
    REPORTING = 5
    REROUTING = 6
    RETURNING = 7
    COMPLETE = 8


# --------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------
# Coordinates travel as int32 of degrees * 1e7, which resolves to ~1 cm and
# costs 4 bytes instead of the 8 a double would.

COORD_SCALE = 1e7


class Payload:
    """Base class for typed payloads."""

    MSG_TYPE: ClassVar[MsgType]

    def encode(self) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def decode(cls, raw: bytes) -> "Payload":  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class HumanDetected(Payload):
    """A geotagged human detection -- the packet the whole system exists for.

    ``lat``/``lon`` are the estimated position of the *person*, not of the
    drone; the detector projects the bounding box onto the ground plane
    before building this payload.
    """

    MSG_TYPE: ClassVar[MsgType] = MsgType.HUMAN_DETECTED
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<iihHB")
    _NO_FIX: ClassVar[int] = -2_147_483_648  # int32 min -- no real coordinate is ever this

    lat: float | None
    lon: float | None
    alt_m: int = 0
    confidence: float = 0.0  # percent, 0.0 - 100.0
    person_count: int = 1

    def encode(self) -> bytes:
        # A camera-only detector has no GPS fix to attach. Sending a
        # fabricated 0.0, 0.0 would be worse than sending nothing -- a
        # rescue team acting on a false position is the exact failure this
        # project exists to prevent. The sentinel keeps the detection
        # itself on the wire (still the life-safety-critical part) while
        # being honest that position is unknown; decode() below reverses
        # it back to None, and the same "no GPS lock" state already shown
        # in the app's own UI is what the sink reports.
        no_fix = self.lat is None or self.lon is None
        return self._STRUCT.pack(
            self._NO_FIX if no_fix else int(round(self.lat * COORD_SCALE)),
            self._NO_FIX if no_fix else int(round(self.lon * COORD_SCALE)),
            max(-32768, min(32767, int(round(self.alt_m)))),
            max(0, min(1000, int(round(self.confidence * 10)))),
            max(0, min(255, int(self.person_count))),
        )

    @classmethod
    def decode(cls, raw: bytes) -> "HumanDetected":
        lat, lon, alt, conf, count = cls._STRUCT.unpack(raw)
        no_fix = lat == cls._NO_FIX or lon == cls._NO_FIX
        return cls(
            lat=None if no_fix else lat / COORD_SCALE,
            lon=None if no_fix else lon / COORD_SCALE,
            alt_m=alt,
            confidence=conf / 10.0,
            person_count=count,
        )

    @property
    def has_fix(self) -> bool:
        return self.lat is not None and self.lon is not None

    def describe(self) -> str:
        where = f"{self.lat:.5f}, {self.lon:.5f}" if self.has_fix else "no GPS fix"
        return f"{self.person_count} person(s) at {where} ({self.confidence:.1f}% confidence)"


@dataclass
class Telemetry(Payload):
    """Periodic drone state for the mission dashboard."""

    MSG_TYPE: ClassVar[MsgType] = MsgType.TELEMETRY
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<iihBBB")

    lat: float
    lon: float
    alt_m: int = 0
    battery_pct: int = 0
    state: MissionState = MissionState.IDLE
    heading_deg: int = 0

    def encode(self) -> bytes:
        return self._STRUCT.pack(
            int(round(self.lat * COORD_SCALE)),
            int(round(self.lon * COORD_SCALE)),
            max(-32768, min(32767, int(round(self.alt_m)))),
            max(0, min(100, int(self.battery_pct))),
            int(self.state),
            int(round(self.heading_deg % 360 / 2)),  # 2-degree resolution
        )

    @classmethod
    def decode(cls, raw: bytes) -> "Telemetry":
        lat, lon, alt, batt, state, heading = cls._STRUCT.unpack(raw)
        return cls(
            lat=lat / COORD_SCALE,
            lon=lon / COORD_SCALE,
            alt_m=alt,
            battery_pct=batt,
            state=MissionState(state) if state in MissionState._value2member_map_ else MissionState.IDLE,
            heading_deg=heading * 2,
        )

    def describe(self) -> str:
        return (
            f"{self.state.name} @ {self.lat:.5f}, {self.lon:.5f} "
            f"{self.alt_m}m hdg {self.heading_deg} batt {self.battery_pct}%"
        )


@dataclass
class NodeStatus(Payload):
    """Health beacon from a relay so the GCS can draw the live mesh."""

    MSG_TYPE: ClassVar[MsgType] = MsgType.NODE_STATUS
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<BIHb")

    battery_pct: int = 0
    uptime_s: int = 0
    forwarded: int = 0
    last_rssi: int = 0

    def encode(self) -> bytes:
        return self._STRUCT.pack(
            max(0, min(100, int(self.battery_pct))),
            max(0, min(0xFFFFFFFF, int(self.uptime_s))),
            max(0, min(0xFFFF, int(self.forwarded))),
            max(-128, min(127, int(self.last_rssi))),
        )

    @classmethod
    def decode(cls, raw: bytes) -> "NodeStatus":
        batt, uptime, fwd, rssi = cls._STRUCT.unpack(raw)
        return cls(battery_pct=batt, uptime_s=uptime, forwarded=fwd, last_rssi=rssi)

    def describe(self) -> str:
        return (
            f"up {self.uptime_s}s, forwarded {self.forwarded}, "
            f"batt {self.battery_pct}%, last RSSI {self.last_rssi} dBm"
        )


@dataclass
class Ack(Payload):
    """Confirms a specific (source, sequence) reached the destination."""

    MSG_TYPE: ClassVar[MsgType] = MsgType.ACK
    _STRUCT: ClassVar[struct.Struct] = struct.Struct("<BH")

    acked_src: int
    acked_seq: int

    def encode(self) -> bytes:
        return self._STRUCT.pack(self.acked_src & 0xFF, self.acked_seq & 0xFFFF)

    @classmethod
    def decode(cls, raw: bytes) -> "Ack":
        src, seq = cls._STRUCT.unpack(raw)
        return cls(acked_src=src, acked_seq=seq)

    def describe(self) -> str:
        return f"ack {config.node_name(self.acked_src)}/SAR-{self.acked_seq:05d}"


@dataclass
class RawPayload(Payload):
    """Fallback for message types with no typed decoder yet."""

    MSG_TYPE: ClassVar[MsgType] = MsgType.HEARTBEAT
    data: bytes = b""

    def encode(self) -> bytes:
        return self.data

    @classmethod
    def decode(cls, raw: bytes) -> "RawPayload":
        return cls(data=bytes(raw))

    def describe(self) -> str:
        return self.data.hex() if self.data else "(empty)"


PAYLOAD_TYPES: dict[int, type[Payload]] = {
    MsgType.HUMAN_DETECTED: HumanDetected,
    MsgType.TELEMETRY: Telemetry,
    MsgType.NODE_STATUS: NodeStatus,
    MsgType.ACK: Ack,
}


# --------------------------------------------------------------------------
# Receive metadata
# --------------------------------------------------------------------------


@dataclass
class RxInfo:
    """Link quality of one received copy.

    Not transmitted -- it is measured locally by the receiving radio.  The
    sink keeps the RxInfo of every copy it hears so it can report which path
    delivered the strongest signal.
    """

    rssi_dbm: float = 0.0
    snr_db: float = 0.0
    received_at: float = field(default_factory=time.time)
    via: int = config.ADDR_UNASSIGNED  # the LAST_HOP that delivered this copy


# --------------------------------------------------------------------------
# Packet
# --------------------------------------------------------------------------


@dataclass
class Packet:
    msg_type: MsgType
    src: int
    dst: int
    seq: int
    payload: Payload
    ttl: int = config.DEFAULT_TTL
    hop: int = 0
    last_hop: int = config.ADDR_UNASSIGNED
    version: int = PROTOCOL_VERSION
    rx: RxInfo | None = None  # populated on receive, never transmitted

    # ---- identity -------------------------------------------------------

    @property
    def uid(self) -> tuple[int, int]:
        """Globally unique id used for duplicate suppression."""
        return (self.src, self.seq)

    @property
    def packet_id(self) -> str:
        """Display form, e.g. ``SAR-00127``."""
        return f"SAR-{self.seq:05d}"

    # ---- wire format ----------------------------------------------------

    def encode(self) -> bytes:
        body = self.payload.encode()
        if len(body) > MAX_PAYLOAD:
            raise PacketError(f"payload too long: {len(body)} > {MAX_PAYLOAD}")
        header = bytes(
            (
                ((self.version & 0x0F) << 4) | (int(self.msg_type) & 0x0F),
                self.src & 0xFF,
                self.dst & 0xFF,
                ((min(self.ttl, MAX_TTL) & 0x0F) << 4) | (min(self.hop, MAX_HOP) & 0x0F),
                self.seq & 0xFF,
                (self.seq >> 8) & 0xFF,
                self.last_hop & 0xFF,
                len(body),
            )
        )
        return header + body

    @classmethod
    def decode(cls, raw: bytes, rx: RxInfo | None = None) -> "Packet":
        if len(raw) < HEADER_SIZE:
            raise PacketError(f"runt frame: {len(raw)} bytes")

        version = raw[0] >> 4
        if version != PROTOCOL_VERSION:
            raise PacketError(f"unsupported protocol version {version}")

        raw_type = raw[0] & 0x0F
        try:
            msg_type = MsgType(raw_type)
        except ValueError as exc:
            raise PacketError(f"unknown message type {raw_type}") from exc

        length = raw[7]
        body = raw[HEADER_SIZE : HEADER_SIZE + length]
        if len(body) != length:
            raise PacketError(f"truncated payload: want {length}, got {len(body)}")

        payload_cls = PAYLOAD_TYPES.get(msg_type, RawPayload)
        try:
            payload = payload_cls.decode(body)
        except (struct.error, ValueError) as exc:
            raise PacketError(f"bad {msg_type.name} payload: {exc}") from exc

        pkt = cls(
            msg_type=msg_type,
            src=raw[1],
            dst=raw[2],
            seq=raw[4] | (raw[5] << 8),
            payload=payload,
            ttl=raw[3] >> 4,
            hop=raw[3] & 0x0F,
            last_hop=raw[6],
            version=version,
            rx=rx,
        )
        if rx is not None:
            rx.via = pkt.last_hop
        return pkt

    @property
    def size_bytes(self) -> int:
        return HEADER_SIZE + len(self.payload.encode())

    # ---- relaying -------------------------------------------------------

    def for_forwarding(self, relay_addr: int) -> "Packet":
        """A copy of this packet as it should leave ``relay_addr``.

        TTL down, hop count up, LAST_HOP rewritten.  SRC and DST are never
        touched, so the origin and destination survive the whole journey.
        """
        return Packet(
            msg_type=self.msg_type,
            src=self.src,
            dst=self.dst,
            seq=self.seq,
            payload=self.payload,
            ttl=self.ttl - 1,
            hop=min(self.hop + 1, MAX_HOP),
            last_hop=relay_addr,
            version=self.version,
        )

    @property
    def route(self) -> str:
        """``DIRECT`` when it arrived straight from the source, else ``RELAY``."""
        return "DIRECT" if self.hop == 0 else "RELAY"

    # ---- display --------------------------------------------------------

    def summary(self) -> str:
        """One-line form for logs and the event timeline."""
        parts = [
            self.packet_id,
            f"{config.node_name(self.src)}->{config.node_name(self.dst)}",
            self.msg_type.name,
            f"hop={self.hop}",
            f"ttl={self.ttl}",
        ]
        if self.hop:
            parts.append(f"via={config.node_name(self.last_hop)}")
        if self.rx:
            parts.append(f"rssi={self.rx.rssi_dbm:.0f}dBm")
            parts.append(f"snr={self.rx.snr_db:.1f}dB")
        return " ".join(parts)

    def report(self) -> str:
        """Multi-line operator-facing form, as shown on the GCS."""
        lines = [
            f"PACKET_ID:   {self.packet_id}",
            f"SOURCE:      {config.node_name(self.src)}",
            f"DESTINATION: {config.node_name(self.dst)}",
            f"TYPE:        {self.msg_type.name}",
            f"HOP:         {self.hop}",
            f"TTL:         {self.ttl}",
            f"ROUTE:       {self.route}",
        ]
        if self.hop:
            lines.append(f"RELAY:       {config.node_name(self.last_hop)}")
        if self.rx:
            lines.append(f"RSSI:        {self.rx.rssi_dbm:.0f} dBm")
            lines.append(f"SNR:         {self.rx.snr_db:.1f} dB")
        describe = getattr(self.payload, "describe", None)
        if describe:
            lines.append(f"PAYLOAD:     {describe()}")
        return "\n".join(lines)


class SequenceGenerator:
    """Monotonic 16-bit sequence counter, one per originating node.

    Wraps at 65535.  Combined with the 1-byte source address this gives the
    duplicate-suppression cache a unique key that survives far longer than
    the cache retention window.
    """

    def __init__(self, start: int = 1) -> None:
        self._next = start & 0xFFFF

    def next(self) -> int:
        seq = self._next
        self._next = (self._next + 1) & 0xFFFF
        if self._next == 0:  # skip 0, it reads badly as "SAR-00000"
            self._next = 1
        return seq
