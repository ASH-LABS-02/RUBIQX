"""Flood-and-deduplicate mesh over LoRa.

This is deliberately *not* a routing protocol.  There are no routing tables,
no neighbour discovery and no path computation.  A node that hears a packet
it is not the destination for simply rebroadcasts it, after a random delay,
until the time-to-live runs out.  Two mechanisms keep that from melting the
channel:

* **Duplicate suppression** -- each node remembers the ``(source, sequence)``
  of everything it has already handled and silently drops repeats, so a
  packet crosses any given node exactly once.
* **Time-to-live** -- every forward decrements TTL, so flood depth is
  bounded no matter how the nodes are arranged.

The advantage over real routing is that it needs no state to be correct and
no convergence time after a node dies -- which, for a mesh of battery relays
scattered across a disaster area, is the property that actually matters.
The cost is redundant transmissions, which at ~20 byte packets we can afford.

Path *selection* happens at the sink instead of in the network: the GCS
hears several copies of the same packet arriving by different routes and
reports the one with the best link quality (see :class:`PathCollector`).
"""

from __future__ import annotations

import heapq
import itertools
import logging
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from . import config
from .packet import (
    MsgType,
    Packet,
    PacketError,
    Payload,
    RxInfo,
    SequenceGenerator,
)
from .radio.base import Radio

log = logging.getLogger("sar.mesh")

MessageHandler = Callable[[Packet], None]

JITTER_AIRTIME_MULTIPLE = 6.0
"""Upper bound of the forwarding jitter window, as a multiple of airtime.

Two relays drawing independent delays from a window W collide whenever the
draws land within one airtime A of each other, with probability roughly
2A/W -- so widening the window has badly diminishing returns, and jitter
alone never gets the collision count near zero. Carrier sense is what
actually does that; jitter is kept because it desynchronises the moment at
which relays decide to sense, which matters on hardware where three nodes
finish receiving the same frame within microseconds of each other.
"""

DUTY_WARN_INTERVAL_S = 30.0
"""Minimum gap between duty-cycle override warnings from one node.

Logging every override buries anything else in the console, which is the
opposite of what a warning is for. The count is kept in stats regardless.
"""

CARRIER_SENSE_ATTEMPTS = 6
CARRIER_SENSE_BACKOFF_S = (0.10, 0.50)
"""Re-check interval when the channel is busy, comparable to one airtime."""


# --------------------------------------------------------------------------
# Duplicate suppression
# --------------------------------------------------------------------------


class SeenCache:
    """Bounded, time-expiring record of packets this node already handled.

    Bounded because a relay may run for hours on a battery and an unbounded
    set is a slow memory leak; time-expiring because sequence numbers wrap
    at 65535 and an ancient entry would wrongly suppress a fresh packet.
    """

    def __init__(
        self,
        retention_s: float = config.DEDUP_RETENTION_S,
        max_entries: int = config.DEDUP_MAX_ENTRIES,
    ) -> None:
        self.retention_s = retention_s
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._lock = threading.Lock()

    def check_and_add(self, uid: tuple[int, int], now: float | None = None) -> bool:
        """Return True if this is the first time we have seen ``uid``."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            if uid in self._entries:
                self._entries.move_to_end(uid)
                return False
            self._entries[uid] = now
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return True

    def _expire(self, now: float) -> None:
        cutoff = now - self.retention_s
        while self._entries:
            uid, stamp = next(iter(self._entries.items()))
            if stamp >= cutoff:
                break
            self._entries.pop(uid)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# --------------------------------------------------------------------------
# Channel occupancy
# --------------------------------------------------------------------------


class DutyCycleTracker:
    """Sliding-window accounting of how much of the channel we are using.

    Shared spectrum only works if everyone leaves gaps.  Ordinary traffic
    that would breach the budget is deferred; emergency traffic is allowed
    through and logged, because a duty-cycle budget is not a reason to sit
    on a survivor's coordinates.
    """

    def __init__(self, limit: float = config.DUTY_CYCLE_LIMIT, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._events: list[tuple[float, float]] = []  # (timestamp, airtime_s)
        self._lock = threading.Lock()

    def _used(self, now: float) -> float:
        cutoff = now - self.window_s
        self._events = [(t, d) for t, d in self._events if t >= cutoff]
        return sum(d for _, d in self._events)

    def would_exceed(self, airtime_ms: float) -> bool:
        now = time.monotonic()
        with self._lock:
            return (self._used(now) + airtime_ms / 1000.0) > (self.window_s * self.limit)

    def record(self, airtime_ms: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._events.append((now, airtime_ms / 1000.0))
            self._used(now)

    @property
    def utilisation(self) -> float:
        now = time.monotonic()
        with self._lock:
            return self._used(now) / self.window_s


# --------------------------------------------------------------------------
# Mesh node
# --------------------------------------------------------------------------


@dataclass
class _Scheduled:
    due: float
    order: int
    packet: Packet
    priority: bool

    def __lt__(self, other: "_Scheduled") -> bool:
        return (self.due, self.order) < (other.due, other.order)


class MeshNode:
    """One participant in the mesh: a drone, a relay, or the ground station.

    The only behavioural difference between the roles is ``forward``.  A
    relay rebroadcasts traffic that is not addressed to it; a drone and the
    GCS sit at the edges of the mesh and do not.
    """

    def __init__(
        self,
        address: int,
        radio: Radio,
        forward: bool = False,
        seen: SeenCache | None = None,
        duty_cycle: DutyCycleTracker | None = None,
        rng: random.Random | None = None,
        jitter_ms: tuple[float, float] | None = None,
        carrier_sense: bool = True,
    ) -> None:
        self.address = address
        self.radio = radio
        self.forward = forward
        self.seen = seen or SeenCache()
        self.duty_cycle = duty_cycle or DutyCycleTracker()
        self.jitter_ms = config.FORWARD_JITTER_MS if jitter_ms is None else jitter_ms
        self.carrier_sense = carrier_sense
        self._rng = rng or random.Random()

        self._seq = SequenceGenerator()
        self._queue: list[_Scheduled] = []
        self._counter = itertools.count()
        self._cv = threading.Condition()
        self._running = False
        self._tx_thread: threading.Thread | None = None

        self._on_message: MessageHandler | None = None
        self._on_copy: MessageHandler | None = None
        self._on_forward: MessageHandler | None = None

        self.stats: dict[str, int] = {
            "originated": 0,
            "transmitted": 0,
            "received": 0,
            "delivered": 0,
            "forwarded": 0,
            "duplicates": 0,
            "ttl_expired": 0,
            "decode_errors": 0,
            "deferred_duty_cycle": 0,
            "deferred_busy": 0,
            "sent_on_busy_channel": 0,
            "duty_cycle_overrides": 0,
        }
        self._duty_warn_at = 0.0
        self._duty_warn_suppressed = 0

    # ---- lifecycle ------------------------------------------------------

    @property
    def name(self) -> str:
        return config.node_name(self.address)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.radio.on_receive(self._handle_frame)
        self.radio.start()
        self._tx_thread = threading.Thread(
            target=self._tx_loop, name=f"tx-{self.name}", daemon=True
        )
        self._tx_thread.start()
        log.info("%s up (forward=%s, profile=%s)", self.name, self.forward, self.radio.profile.name)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        with self._cv:
            self._cv.notify_all()
        if self._tx_thread:
            self._tx_thread.join(timeout=5.0)
        self.radio.stop()
        log.info("%s down: %s", self.name, self.stats)

    def __enter__(self) -> "MeshNode":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ---- handlers -------------------------------------------------------

    def on_message(self, callback: MessageHandler) -> None:
        """Called once per unique packet addressed to this node."""
        self._on_message = callback

    def on_copy(self, callback: MessageHandler) -> None:
        """Called for *every* copy heard, duplicates included.

        The sink uses this to compare the routes a packet arrived by.
        """
        self._on_copy = callback

    def on_forward(self, callback: MessageHandler) -> None:
        """Called when this node decides to relay a packet."""
        self._on_forward = callback

    # ---- originating ----------------------------------------------------

    def send(
        self,
        msg_type: MsgType,
        payload: Payload,
        dst: int = config.ADDR_GCS,
        ttl: int = config.DEFAULT_TTL,
        priority: bool = False,
    ) -> Packet:
        """Originate a packet.  Returns immediately; the radio sends it soon.

        ``priority`` marks life-safety traffic: it jumps the queue and
        ignores the duty-cycle budget.
        """
        pkt = Packet(
            msg_type=msg_type,
            src=self.address,
            dst=dst,
            seq=self._seq.next(),
            payload=payload,
            ttl=ttl,
            hop=0,
            last_hop=self.address,
        )
        # Our own packet must never be re-handled if a relay echoes it back.
        self.seen.check_and_add(pkt.uid)
        self.stats["originated"] += 1
        self._enqueue(pkt, delay_s=0.0, priority=priority)
        return pkt

    # ---- receiving ------------------------------------------------------

    def _handle_frame(self, data: bytes, rx: RxInfo) -> None:
        try:
            pkt = Packet.decode(data, rx)
        except PacketError as exc:
            self.stats["decode_errors"] += 1
            log.debug("%s dropped malformed frame: %s", self.name, exc)
            return

        self.stats["received"] += 1

        if self._on_copy:
            self._on_copy(pkt)

        if pkt.src == self.address:
            return  # our own packet, echoed by a relay

        if not self.seen.check_and_add(pkt.uid):
            self.stats["duplicates"] += 1
            log.debug("%s dropped duplicate %s", self.name, pkt.packet_id)
            return

        if pkt.dst in (self.address, config.ADDR_BROADCAST):
            self.stats["delivered"] += 1
            log.info("%s accepted %s", self.name, pkt.summary())
            if self._on_message:
                self._on_message(pkt)
            if pkt.dst == self.address:
                return  # terminal for us; broadcasts still propagate

        if not self.forward:
            return

        if pkt.ttl <= 1:
            self.stats["ttl_expired"] += 1
            log.debug("%s expired %s (ttl exhausted)", self.name, pkt.packet_id)
            return

        relayed = pkt.for_forwarding(self.address)
        delay = self._forward_delay(relayed.size_bytes)
        log.info(
            "%s relaying %s in %.0f ms (hop %d, ttl %d)",
            self.name, relayed.packet_id, delay * 1000, relayed.hop, relayed.ttl,
        )
        self.stats["forwarded"] += 1
        if self._on_forward:
            self._on_forward(relayed)
        self._enqueue(relayed, delay_s=delay, priority=relayed.msg_type == MsgType.HUMAN_DETECTED)

    def _forward_delay(self, size_bytes: int) -> float:
        """Random hold-off before rebroadcasting, in seconds.

        LoRa has no carrier sense, so every relay that hears a packet would
        otherwise retransmit at the same instant and collide.  The window
        scales with airtime -- a fixed 400 ms is plenty at SF7 but useless
        at SF12, where a single frame occupies the channel for 1.3 s.
        """
        if self.jitter_ms == (0.0, 0.0):
            return 0.0  # deliberately disabled, to demonstrate why it exists
        airtime = self.radio.airtime_ms(size_bytes)
        low = max(self.jitter_ms[0], airtime * 0.25)
        high = max(self.jitter_ms[1], airtime * JITTER_AIRTIME_MULTIPLE)
        return self._rng.uniform(low, high) / 1000.0

    def _warn_over_budget(self, packet: Packet) -> None:
        """Note an emergency packet sent past the duty-cycle budget.

        Life-safety traffic is never held back for airtime accounting, but a
        node that is *persistently* over budget is telling you something:
        routine traffic is crowding the channel the emergency packets need.
        The right response is to send less telemetry, not to raise the limit.
        """
        self.stats["duty_cycle_overrides"] += 1
        self._duty_warn_suppressed += 1

        now = time.monotonic()
        if now - self._duty_warn_at < DUTY_WARN_INTERVAL_S:
            return

        suppressed = self._duty_warn_suppressed - 1
        self._duty_warn_at = now
        self._duty_warn_suppressed = 0

        log.warning(
            "%s over duty-cycle budget (%.0f%% used) -- sending emergency %s anyway%s. "
            "Reduce telemetry rate if this persists.",
            self.name,
            self.duty_cycle.utilisation * 100,
            packet.packet_id,
            f"; {suppressed} more in the last {DUTY_WARN_INTERVAL_S:.0f}s" if suppressed else "",
        )

    def _wait_for_clear_channel(self) -> bool:
        """Listen before talking.  Returns False if the channel stayed busy.

        Measured with ``tools/simulate.py collision``: this is the mechanism
        that actually eliminates collisions among relays hearing the same
        packet (22 collisions down to 0 in a three-relay flood). Forwarding
        jitter on its own barely dents that count -- it rescues delivery
        only because a redundant copy gets through by luck.
        """
        if not self.carrier_sense:
            return True
        for _ in range(CARRIER_SENSE_ATTEMPTS):
            if not self.radio.channel_busy():
                return True
            self.stats["deferred_busy"] += 1
            time.sleep(self._rng.uniform(*CARRIER_SENSE_BACKOFF_S))
        return not self.radio.channel_busy()

    # ---- transmitting ---------------------------------------------------

    def _enqueue(self, pkt: Packet, delay_s: float, priority: bool = False) -> None:
        item = _Scheduled(
            due=time.monotonic() + delay_s,
            order=next(self._counter),
            packet=pkt,
            priority=priority,
        )
        with self._cv:
            heapq.heappush(self._queue, item)
            self._cv.notify()

    def _tx_loop(self) -> None:
        while self._running:
            with self._cv:
                while self._running and not self._queue:
                    self._cv.wait(timeout=0.5)
                if not self._running:
                    return
                wait = self._queue[0].due - time.monotonic()
                if wait > 0:
                    self._cv.wait(timeout=wait)
                    continue
                item = heapq.heappop(self._queue)

            self._transmit(item)

    def _transmit(self, item: _Scheduled) -> None:
        data = item.packet.encode()
        airtime = self.radio.airtime_ms(len(data))

        if self.duty_cycle.would_exceed(airtime):
            if item.priority:
                self._warn_over_budget(item.packet)
            else:
                self.stats["deferred_duty_cycle"] += 1
                item.due = time.monotonic() + 1.0
                with self._cv:
                    heapq.heappush(self._queue, item)
                    self._cv.notify()
                return

        if not self._wait_for_clear_channel():
            # Give up sensing rather than sit on the packet forever: a stuck
            # carrier would otherwise silence this node for the whole mission.
            self.stats["sent_on_busy_channel"] += 1
            log.warning(
                "%s transmitting %s into a busy channel after %d attempts",
                self.name, item.packet.packet_id, CARRIER_SENSE_ATTEMPTS,
            )

        try:
            self.radio.send(data)
        except Exception:  # a radio fault must not kill the TX thread
            log.exception("%s transmit failed for %s", self.name, item.packet.packet_id)
            return

        self.duty_cycle.record(airtime)
        self.stats["transmitted"] += 1


# --------------------------------------------------------------------------
# Sink-side path selection
# --------------------------------------------------------------------------


@dataclass
class PathReport:
    """What the GCS learned about how one packet reached it."""

    packet: Packet
    copies: list[Packet] = field(default_factory=list)

    @property
    def best(self) -> Packet:
        """The copy that took the shortest path, strongest signal breaking ties.

        Hop count is ranked ahead of signal strength deliberately: a strong
        three-hop copy still crossed three battery-powered relays that could
        each fail, and took three airtimes to arrive. A weaker direct path
        that is decoding correctly is the better one to report to the
        operator, and the full list of routes is available alongside it.
        """
        return min(
            self.copies,
            key=lambda p: (p.hop, -(p.rx.rssi_dbm if p.rx else -999)),
        )

    @property
    def routes(self) -> list[str]:
        out = []
        for c in sorted(self.copies, key=lambda p: p.hop):
            via = config.node_name(c.last_hop)
            rssi = f"{c.rx.rssi_dbm:.0f}" if c.rx else "?"
            out.append(f"{c.route} via {via} ({c.hop} hop, {rssi} dBm)")
        return out

    def summary(self) -> str:
        best = self.best
        return (
            f"{best.packet_id} delivered {best.route.lower()} in {best.hop} hop(s) "
            f"via {config.node_name(best.last_hop)}; {len(self.copies)} copy/copies heard"
        )


class PathCollector:
    """Waits a moment after a packet arrives to see what else turns up.

    A flood delivers the same packet by every route that works.  The first
    copy is the fastest, but not necessarily the strongest -- so we hold the
    packet open for a short window, gather every copy, and then report which
    path was actually best.  That is what the dashboard shows as ROUTE and
    HOPS, and it is measured rather than assumed.
    """

    def __init__(
        self,
        window_s: float = config.SINK_COLLECT_WINDOW_S,
        on_report: Callable[[PathReport], None] | None = None,
    ) -> None:
        self.window_s = window_s
        self._on_report = on_report
        self._open: dict[tuple[int, int], PathReport] = {}
        self._deadlines: dict[tuple[int, int], float] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._reap_loop, name="path-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self.flush()

    def record(self, pkt: Packet) -> None:
        """Feed every copy heard, including duplicates."""
        with self._lock:
            report = self._open.get(pkt.uid)
            if report is None:
                report = PathReport(packet=pkt)
                self._open[pkt.uid] = report
                self._deadlines[pkt.uid] = time.monotonic() + self.window_s
            report.copies.append(pkt)

    def _reap_loop(self) -> None:
        while self._running:
            time.sleep(0.25)
            self._emit_due()

    def _emit_due(self) -> None:
        now = time.monotonic()
        with self._lock:
            due = [uid for uid, dl in self._deadlines.items() if dl <= now]
            reports = [self._open.pop(uid) for uid in due]
            for uid in due:
                self._deadlines.pop(uid, None)
        for report in reports:
            if self._on_report:
                self._on_report(report)

    def flush(self) -> None:
        with self._lock:
            reports = list(self._open.values())
            self._open.clear()
            self._deadlines.clear()
        for report in reports:
            if self._on_report:
                self._on_report(report)
