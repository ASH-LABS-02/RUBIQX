"""Static configuration for the SAR LoRa mesh.

Node IDs are the on-air identity and are exactly one byte.  Anything
human-readable (``DRONE-01``) is looked up from NODE_NAMES so the wire
format stays small -- LoRa airtime is the scarcest resource in this system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Node addressing
# --------------------------------------------------------------------------
# 0x00          reserved / unassigned
# 0x01 - 0x0F   drones
# 0x10 - 0x7F   relay nodes
# 0xF0          ground control station
# 0xFF          broadcast

ADDR_UNASSIGNED = 0x00
ADDR_DRONE_01 = 0x01
ADDR_DRONE_02 = 0x02
ADDR_NODE_01 = 0x10
ADDR_NODE_02 = 0x11
ADDR_NODE_03 = 0x12
ADDR_GCS = 0xF0
ADDR_BROADCAST = 0xFF

NODE_NAMES = {
    ADDR_UNASSIGNED: "UNASSIGNED",
    ADDR_DRONE_01: "DRONE-01",
    ADDR_DRONE_02: "DRONE-02",
    ADDR_NODE_01: "NODE-01",
    ADDR_NODE_02: "NODE-02",
    ADDR_NODE_03: "NODE-03",
    ADDR_GCS: "GCS",
    ADDR_BROADCAST: "BROADCAST",
}


def node_name(addr: int) -> str:
    """Human-readable name for an address, for logs and the dashboard."""
    return NODE_NAMES.get(addr, f"0x{addr:02X}")


def is_relay(addr: int) -> bool:
    return 0x10 <= addr <= 0x7F


# --------------------------------------------------------------------------
# Radio profiles
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RadioProfile:
    """A complete SX127x LoRa configuration.

    Spreading factor is the single biggest lever on range.  SF7 has the
    highest data rate and the *shortest* range; SF10-SF12 trade airtime for
    a much larger link budget.  Our packets are ~20 bytes, so even SF12
    costs well under two seconds on air -- for a search-and-rescue link
    that is a trade worth making.
    """

    name: str
    frequency_hz: int
    bandwidth_hz: int
    spreading_factor: int
    coding_rate: int  # denominator of 4/x, so 5..8
    tx_power_dbm: int
    preamble_length: int = 8
    sync_word: int = 0x12  # 0x12 = private network (0x34 is reserved for LoRaWAN)
    crc: bool = True
    explicit_header: bool = True

    @property
    def low_data_rate_optimize(self) -> bool:
        """Mandatory when a symbol lasts longer than 16 ms (SF11/SF12 @ 125 kHz)."""
        return (2 ** self.spreading_factor) / self.bandwidth_hz > 0.016

    def airtime_ms(self, payload_bytes: int) -> float:
        """Time-on-air, per the SX1276 datasheet (section 4.1.1.7).

        Used for duty-cycle accounting and to size the forwarding jitter --
        jitter has to be wide enough that two relays hearing the same packet
        do not retransmit into each other.
        """
        sf = self.spreading_factor
        t_sym = (2 ** sf) / self.bandwidth_hz
        t_preamble = (self.preamble_length + 4.25) * t_sym

        de = 1 if self.low_data_rate_optimize else 0
        ih = 0 if self.explicit_header else 1
        crc = 1 if self.crc else 0

        numerator = 8 * payload_bytes - 4 * sf + 28 + 16 * crc - 20 * ih
        denominator = 4 * (sf - 2 * de)
        n_payload = 8 + max(math.ceil(numerator / denominator) * (self.coding_rate), 0)

        return (t_preamble + n_payload * t_sym) * 1000.0

    def packets_per_minute(self, payload_bytes: int = 21, duty_cycle: float = 0.10) -> float:
        """How many frames one node may transmit per minute within its budget.

        This is the number that actually bounds the system, and it is much
        smaller than people expect: spreading factor buys range by making
        every packet occupy the channel for longer, so the same duty-cycle
        budget buys far fewer packets. At SF12 a node gets about four
        transmissions a minute -- and a relay spends one of those on every
        packet it forwards, not just on its own.
        """
        return (60_000.0 * duty_cycle) / self.airtime_ms(payload_bytes)

    def sensitivity_dbm(self) -> float:
        """Approximate receiver sensitivity for this configuration.

        noise floor = -174 + 10*log10(BW) + NF, plus the SNR the demodulator
        needs at this spreading factor.  Used by the simulator to decide
        whether a node can hear a transmission.
        """
        required_snr = {7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
        noise_figure = 6.0
        noise_floor = -174 + 10 * math.log10(self.bandwidth_hz) + noise_figure
        return noise_floor + required_snr.get(self.spreading_factor, -7.5)


# The configuration our RA-02 (SX1278) nodes were first brought up on.
# Fast and convenient on a bench, but the worst case for range.
# TX power on all three profiles: India's 2022 DoT exemption for
# 433.05-434.79 MHz permits up to 10 mW ERP (license-exempt) -- ERP already
# accounts for antenna gain, so conducted power has to sit below that once
# a ~2 dBi whip is on the module. 2 dBm conducted (~1.6 mW) matches the
# figure independently verified against this same citation and confirmed
# working over an actual two-hop bench link on this hardware; see
# docs/BRINGUP.md. The previous defaults here (14-17 dBm) were 20-45x over
# this limit and must not be transmitted outdoors on 433 MHz in India.
#   DoT: https://eservices.dot.gov.in/sites/default/files/circular-notifications/
#        the-use-of-low-power-radio-frequency-devices-in-the-frequency-band-
#        433.05-to-434.79-mhz-exemption-from-license-rules-2022.pdf
# This is not a substitute for checking your own antenna's gain and the
# current rule text before transmitting.
LEGAL_TX_POWER_DBM_INDIA_433 = 2

BENCH = RadioProfile(
    name="BENCH",
    frequency_hz=433_000_000,
    bandwidth_hz=125_000,
    spreading_factor=7,
    coding_rate=5,
    tx_power_dbm=LEGAL_TX_POWER_DBM_INDIA_433,
)

# What an actual deployment should use.  ~10 dB more link budget than BENCH
# for about 6x the airtime, which our packet sizes can easily absorb.
FIELD = RadioProfile(
    name="FIELD",
    frequency_hz=433_000_000,
    bandwidth_hz=125_000,
    spreading_factor=10,
    coding_rate=5,
    tx_power_dbm=LEGAL_TX_POWER_DBM_INDIA_433,
)

# Maximum reach, for the widest-spaced relay hop in the chain.
LONG_RANGE = RadioProfile(
    name="LONG_RANGE",
    frequency_hz=433_000_000,
    bandwidth_hz=125_000,
    spreading_factor=12,
    coding_rate=5,
    tx_power_dbm=LEGAL_TX_POWER_DBM_INDIA_433,
)

PROFILES = {p.name: p for p in (BENCH, FIELD, LONG_RANGE)}
DEFAULT_PROFILE = FIELD

# NOTE: all three profiles sit at 433 MHz because the RA-02 is a 433 MHz
# module.  India's license-exempt LPWAN band with the generous power limit is
# 865-867 MHz, which needs different hardware (e.g. RA-01H / SX1276 868).
# Confirm the applicable WPC limits before any outdoor deployment.

# --------------------------------------------------------------------------
# Mesh behaviour
# --------------------------------------------------------------------------

DEFAULT_TTL = 5
"""Hops a packet may take before it is discarded.  Bounds flood depth."""

DEDUP_RETENTION_S = 180.0
"""How long a (source, sequence) pair stays in the seen-cache."""

DEDUP_MAX_ENTRIES = 512
"""Hard cap on the seen-cache so a long mission cannot exhaust memory."""

FORWARD_JITTER_MS = (20, 400)
"""Random delay before retransmitting.

LoRa is half-duplex with no carrier sense.  Two relays that hear the same
packet will otherwise forward at the same instant and destroy each other's
transmission.  The window must comfortably exceed one packet's airtime.
"""

SINK_COLLECT_WINDOW_S = 4.0
"""After the first copy of a packet reaches the sink, how long to keep
listening for further copies before declaring a best path."""

DUTY_CYCLE_LIMIT = 0.10
"""Fraction of wall-clock time a node may occupy the channel.  Advisory --
enforced by MeshNode, which defers transmissions that would exceed it."""
