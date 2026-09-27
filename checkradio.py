#!/usr/bin/env python3
"""Staged hardware diagnostic for a LoRa node.

Run this on every box before you try to run the mesh. It works upward from
"is SPI even enabled" to "can this radio transmit", and stops at the first
stage that fails with a specific thing to go and check.

Stages 1-5 deliberately talk to the chip with raw spidev and gpiozero
rather than through our driver, so that a failure there means a wiring or
OS problem and nothing else. Only from stage 6 is our own driver involved.
That separation is the point: when the mesh is silent you need to know
whether to look at your soldering or at the code.

    python3 tools/checkradio.py
    python3 tools/checkradio.py --transmit      # also send a test frame
    python3 tools/checkradio.py --listen 30     # also listen for 30 s
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sar import config

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"

REG_OP_MODE = 0x01
REG_PREAMBLE_LSB = 0x21
REG_VERSION = 0x42
EXPECTED_VERSION = 0x12


class StageFailed(Exception):
    """Carries the remedy, not just the symptom."""

    def __init__(self, problem: str, remedy: str) -> None:
        super().__init__(problem)
        self.problem = problem
        self.remedy = remedy


def stage(number: int, title: str) -> None:
    print(f"\n{DIM}[{number}]{RESET} {title}")


def ok(message: str) -> None:
    print(f"    {GREEN}PASS{RESET}  {message}")


def warn(message: str) -> None:
    print(f"    {YELLOW}NOTE{RESET}  {message}")


# --------------------------------------------------------------------------
# Stages 1-5: the hardware, without our driver
# --------------------------------------------------------------------------


def check_imports() -> tuple:
    stage(1, "Python libraries")
    try:
        import spidev
    except ImportError:
        raise StageFailed(
            "spidev is not installed",
            "pip install spidev gpiozero lgpio",
        )
    try:
        from gpiozero import DigitalInputDevice, DigitalOutputDevice
    except ImportError:
        raise StageFailed(
            "gpiozero is not installed",
            "pip install gpiozero lgpio\n"
            "          Do NOT install RPi.GPIO instead -- it does not work on the Pi 5.",
        )
    ok("spidev and gpiozero import")
    return spidev, DigitalInputDevice, DigitalOutputDevice


def check_spi_device(bus: int, device: int) -> None:
    stage(2, "SPI device node")
    path = Path(f"/dev/spidev{bus}.{device}")
    if not path.exists():
        present = sorted(p.name for p in Path("/dev").glob("spidev*"))
        raise StageFailed(
            f"{path} does not exist"
            + (f" (found: {', '.join(present)})" if present else " (no spidev nodes at all)"),
            "Enable SPI:  sudo raspi-config  ->  Interface Options  ->  SPI  ->  Yes\n"
            "          then reboot. Confirm with:  ls /dev/spidev*",
        )
    ok(f"{path} present")


def check_reset_pin(DigitalOutputDevice, pin: int):
    stage(3, f"RESET line on GPIO{pin}")
    try:
        reset = DigitalOutputDevice(pin, initial_value=True)
    except Exception as exc:
        raise StageFailed(
            f"cannot claim GPIO{pin}: {exc}",
            "Another process may hold the pin. Check nothing else is running,\n"
            "          and that the pin number is BCM numbering, not physical.",
        )
    reset.off()
    time.sleep(0.01)
    reset.on()
    time.sleep(0.01)
    ok("reset pulse driven")
    return reset


def check_chip_id(spi) -> None:
    stage(4, "Chip identification over SPI")
    version = spi.xfer2([REG_VERSION & 0x7F, 0x00])[1]

    if version == EXPECTED_VERSION:
        ok(f"REG_VERSION = 0x{version:02X} -- SX1276/77/78/79 responding")
        return

    if version in (0x00, 0xFF):
        raise StageFailed(
            f"REG_VERSION = 0x{version:02X} -- the radio is not answering at all",
            "0x00 or 0xFF means SPI is talking to nothing. In order of likelihood:\n"
            "          - MISO not connected (this is the usual one)\n"
            "          - NSS/CS on the wrong pin, or not CE0\n"
            "          - module not powered, or powered from 5V instead of 3.3V\n"
            "          - MOSI and MISO swapped\n"
            "          Check continuity on MISO first.",
        )

    raise StageFailed(
        f"REG_VERSION = 0x{version:02X} -- something answered, but it is not an SX127x",
        "A plausible-but-wrong value usually means a marginal connection or\n"
        "          too high an SPI clock. Try --spi-speed 500000 and re-run.",
    )


def check_register_write(spi) -> None:
    stage(5, "Register write and read-back")
    original = spi.xfer2([REG_PREAMBLE_LSB & 0x7F, 0x00])[1]

    for pattern in (0xA5, 0x5A):
        spi.xfer2([REG_PREAMBLE_LSB | 0x80, pattern])
        read = spi.xfer2([REG_PREAMBLE_LSB & 0x7F, 0x00])[1]
        if read != pattern:
            spi.xfer2([REG_PREAMBLE_LSB | 0x80, original])
            raise StageFailed(
                f"wrote 0x{pattern:02X}, read back 0x{read:02X}",
                "Reads work but writes do not land. Almost always MOSI --\n"
                "          check that line, and check the SPI clock is not too fast.",
            )

    spi.xfer2([REG_PREAMBLE_LSB | 0x80, original])
    ok("SPI is bidirectional -- writes land and read back correctly")


# --------------------------------------------------------------------------
# Stages 6+: our driver
# --------------------------------------------------------------------------


def check_driver(profile, args) -> "object":
    stage(6, "Driver configuration")
    from sar.radio.sx127x import SX127xRadio

    radio = SX127xRadio(
        profile=profile,
        spi_bus=args.spi_bus,
        spi_device=args.spi_device,
        reset_pin=args.reset_pin,
        dio0_pin=args.dio0_pin,
        spi_speed_hz=args.spi_speed,
    )
    try:
        radio.start()
    except Exception as exc:
        raise StageFailed(
            f"driver start() failed: {exc}",
            "Stages 1-5 passed, so the wiring is sound and this is the driver\n"
            "          or the DIO0 pin. Re-run with --dio0-pin set correctly.",
        )
    ok(f"configured for {profile.name}")
    return radio


def verify_registers(radio, profile) -> None:
    stage(7, "Read back what the driver actually wrote")
    from sar.radio import sx127x as R

    mc1 = radio._read(R.REG_MODEM_CONFIG_1)
    mc2 = radio._read(R.REG_MODEM_CONFIG_2)
    mc3 = radio._read(R.REG_MODEM_CONFIG_3)
    sync = radio._read(R.REG_SYNC_WORD)

    bw_index = mc1 >> 4
    coding_rate = ((mc1 >> 1) & 0x07) + 4
    spreading = mc2 >> 4
    crc_on = bool(mc2 & 0x04)
    ldo = bool(mc3 & 0x08)

    expected = {
        "bandwidth": (R.BANDWIDTHS[bw_index], profile.bandwidth_hz),
        "spreading factor": (spreading, profile.spreading_factor),
        "coding rate": (coding_rate, profile.coding_rate),
        "CRC": (crc_on, profile.crc),
        "sync word": (sync, profile.sync_word),
        "low data rate opt": (ldo, profile.low_data_rate_optimize),
    }

    bad = []
    for name, (actual, want) in expected.items():
        mark = GREEN + "ok" + RESET if actual == want else RED + "NO" + RESET
        shown = f"0x{actual:02X}" if name == "sync word" else actual
        wanted = f"0x{want:02X}" if name == "sync word" else want
        print(f"    {mark}  {name:<20} {shown}  (want {wanted})")
        if actual != want:
            bad.append(name)

    if bad:
        raise StageFailed(
            f"driver did not apply: {', '.join(bad)}",
            "The radio is healthy but the driver is configuring it wrongly.\n"
            "          This is a code bug, not a wiring one.",
        )
    ok("every setting matches the profile")

    airtime = profile.airtime_ms(21)
    warn(f"a 21-byte detection packet will occupy the channel for {airtime:.0f} ms")


def check_noise_floor(radio) -> None:
    stage(8, "Receiver and channel")
    from sar.radio import sx127x as R

    offset = 164 if radio.profile.frequency_hz < 779_000_000 else 157
    samples = []
    for _ in range(20):
        samples.append(radio._read(R.REG_RSSI_VALUE) - offset)
        time.sleep(0.02)

    lowest, highest = min(samples), max(samples)
    median = sorted(samples)[len(samples) // 2]
    print(f"    {DIM}ambient RSSI over 20 samples: {lowest} to {highest} dBm, median {median}{RESET}")

    if highest == lowest:
        raise StageFailed(
            f"RSSI is frozen at {highest} dBm",
            "A reading that never moves means the receiver is not running.\n"
            "          Check the driver left the chip in RX continuous mode.",
        )

    sensitivity = radio.profile.sensitivity_dbm()
    if median > sensitivity + 20:
        warn(
            f"the channel is busy -- median {median} dBm sits well above the "
            f"{sensitivity:.0f} dBm sensitivity floor."
        )
        warn("Something else is transmitting nearby. Carrier sense will back off constantly.")
    else:
        ok(f"noise floor looks sane against a {sensitivity:.0f} dBm sensitivity")


def do_transmit(radio) -> None:
    stage(9, "Transmit")
    from sar.mesh import CARRIER_SENSE_ATTEMPTS, CARRIER_SENSE_BACKOFF_S, MeshNode
    from sar.packet import HumanDetected, MsgType

    node = MeshNode(address=config.ADDR_DRONE_01, radio=radio, forward=False)
    node.start()  # before send(): the queue is a plain heap either order works,
    # but starting first means the TX thread genuinely exists before anything
    # is asked of it, rather than relying on that being harmless by design.

    print(f"    {DIM}sending one HUMAN_DETECTED frame...{RESET}")
    started = time.monotonic()
    packet = node.send(
        MsgType.HUMAN_DETECTED,
        HumanDetected(lat=11.0168, lon=76.9558, alt_m=40, confidence=94.2),
        dst=config.ADDR_GCS,
    )

    # A noisy channel can force the full CARRIER_SENSE_ATTEMPTS retries
    # before the node gives up sensing and transmits anyway (this is
    # correct behaviour, not a fault -- see mesh.py's own reasoning on
    # priority traffic). Worst case that alone is several seconds, on top
    # of one airtime -- waiting only airtime+1s here previously declared a
    # perfectly good transmission a FAIL because it checked before the
    # carrier-sense backoff had even finished. Poll instead of one blind
    # sleep, so this both tolerates the worst case and returns quickly
    # when the channel is actually clear.
    worst_case_s = CARRIER_SENSE_ATTEMPTS * CARRIER_SENSE_BACKOFF_S[1] + radio.profile.airtime_ms(21) / 1000.0
    deadline = time.monotonic() + worst_case_s + 1.0
    while time.monotonic() < deadline and node.stats["transmitted"] < 1:
        time.sleep(0.05)
    elapsed = (time.monotonic() - started) * 1000

    if node.stats["transmitted"] < 1:
        node.stop()
        raise StageFailed(
            "the frame never left the transmit queue",
            "If it sat in carrier sense the channel is busy -- see stage 8.",
        )

    ok(f"{packet.packet_id} transmitted, {packet.size_bytes} bytes, {elapsed:.0f} ms wall time")
    if node.stats["sent_on_busy_channel"]:
        warn(
            f"channel read as busy for all {CARRIER_SENSE_ATTEMPTS} carrier-sense attempts "
            f"before this sent anyway -- your ambient RSSI (stage 8) may be close to the "
            f"busy threshold; noisy, not necessarily broken."
        )
    warn("If a second node is listening it should have printed this packet id.")
    node.stop()


def do_listen(radio, seconds: float) -> None:
    stage(10, f"Listen for {seconds:.0f} s")
    from sar.mesh import MeshNode

    heard: list = []
    node = MeshNode(address=config.ADDR_GCS, radio=radio, forward=False)
    node.on_copy(heard.append)
    node.start()

    print(f"    {DIM}listening -- transmit from another node now{RESET}")
    deadline = time.monotonic() + seconds
    seen = 0
    try:
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if len(heard) > seen:
                for pkt in heard[seen:]:
                    print(f"    {GREEN}RX{RESET}    {pkt.summary()}")
                seen = len(heard)
    finally:
        node.stop()

    if not heard:
        warn("nothing heard.")
        warn("If another node was definitely transmitting, check that both ends")
        warn("agree on frequency, spreading factor, bandwidth and sync word --")
        warn("run this script on both and compare stage 7.")
    else:
        ok(f"{len(heard)} frame(s) received")


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(config.PROFILES),
                        default=config.DEFAULT_PROFILE.name)
    parser.add_argument("--reset-pin", type=int, default=25)
    parser.add_argument("--dio0-pin", type=int, default=24)
    parser.add_argument("--spi-bus", type=int, default=0)
    parser.add_argument("--spi-device", type=int, default=0)
    parser.add_argument("--spi-speed", type=int, default=5_000_000)
    parser.add_argument("--transmit", action="store_true", help="send one test frame")
    parser.add_argument("--listen", type=float, metavar="SECONDS",
                        help="listen for incoming frames")
    args = parser.parse_args(argv)

    profile = config.PROFILES[args.profile]
    print(f"LoRa node check -- profile {profile.name}, "
          f"{profile.frequency_hz/1e6:.1f} MHz SF{profile.spreading_factor}")

    spi = reset = radio = None
    try:
        spidev, _, DigitalOutputDevice = check_imports()
        check_spi_device(args.spi_bus, args.spi_device)
        reset = check_reset_pin(DigitalOutputDevice, args.reset_pin)

        spi = spidev.SpiDev()
        spi.open(args.spi_bus, args.spi_device)
        spi.max_speed_hz = args.spi_speed
        spi.mode = 0

        check_chip_id(spi)
        check_register_write(spi)

        # Hand the hardware over to the driver.
        spi.close()
        spi = None
        reset.close()
        reset = None

        radio = check_driver(profile, args)
        verify_registers(radio, profile)
        check_noise_floor(radio)

        if args.transmit:
            do_transmit(radio)
        if args.listen:
            do_listen(radio, args.listen)

        print(f"\n{GREEN}This node is healthy.{RESET}")
        if not (args.transmit or args.listen):
            print(f"{DIM}Next: run with --listen 60 here and --transmit on another node.{RESET}")
        return 0

    except StageFailed as failure:
        print(f"    {RED}FAIL{RESET}  {failure.problem}")
        print(f"\n{YELLOW}What to check:{RESET}\n          {failure.remedy}")
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    finally:
        for resource in (radio, spi, reset):
            try:
                if resource is not None:
                    resource.close() if hasattr(resource, "close") else resource.stop()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
