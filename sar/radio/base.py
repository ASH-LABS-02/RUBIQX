"""Radio interface shared by the real SX127x and the simulator.

MeshNode is written against this interface only, so exactly the same mesh
logic runs on a bench with four RA-02 modules and on a laptop with none.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..config import RadioProfile
from ..packet import RxInfo

ReceiveCallback = Callable[[bytes, RxInfo], None]


class Radio(ABC):
    """A half-duplex LoRa transceiver."""

    def __init__(self, profile: RadioProfile) -> None:
        self.profile = profile
        self._on_receive: ReceiveCallback | None = None

    def on_receive(self, callback: ReceiveCallback) -> None:
        """Register the handler for successfully CRC-checked frames."""
        self._on_receive = callback

    def _deliver(self, data: bytes, rx: RxInfo) -> None:
        if self._on_receive is not None:
            self._on_receive(data, rx)

    @abstractmethod
    def start(self) -> None:
        """Bring the radio up and begin listening."""

    @abstractmethod
    def stop(self) -> None:
        """Shut the radio down and release hardware resources."""

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Transmit one frame.  Blocks until the frame is fully on air.

        The radio is deaf for the whole duration -- this is the half-duplex
        constraint that makes forwarding jitter necessary.
        """

    def channel_busy(self) -> bool:
        """True if someone else is transmitting right now.

        Listen-before-talk, and the mechanism that actually keeps relays off
        each other. Random forwarding jitter alone is not enough: with a
        window a few multiples of airtime wide, two relays drawing
        independent delays still overlap most of the time.

        Radios that cannot sense the channel return False and fall back to
        jitter alone.
        """
        return False

    def airtime_ms(self, payload_bytes: int) -> float:
        return self.profile.airtime_ms(payload_bytes)

    def __enter__(self) -> "Radio":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
