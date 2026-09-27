"""Driver for the SX1276/77/78/79 in LoRa mode (Ai-Thinker RA-02).

Talks SPI via ``spidev`` and GPIO via ``gpiozero``.

.. note::
   On a Raspberry Pi 5 the GPIO lines sit behind the RP1 southbridge and
   the old ``RPi.GPIO`` library does **not** work -- it will import and then
   fail at runtime.  ``gpiozero`` picks the correct backend (``lgpio``)
   automatically on both Pi 4 and Pi 5, which is why it is used here.

Default wiring, matching the prototype::

    RA-02        Raspberry Pi 5
    VCC          3.3V    pin 17     (NOT 5V -- the module is 3.3V only)
    GND          GND     pin 20
    SCK          GPIO11  pin 23
    MISO         GPIO9   pin 21
    MOSI         GPIO10  pin 19
    NSS/CS       GPIO8   pin 24     (CE0)
    RESET        GPIO25  pin 22
    DIO0         GPIO24  pin 18     (RxDone interrupt)

Enable SPI first with ``sudo raspi-config`` -> Interface Options -> SPI.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import RadioProfile
from ..packet import RxInfo
from .base import Radio

log = logging.getLogger("sar.radio.sx127x")

# ---- registers -----------------------------------------------------------

REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_FRF_MSB = 0x06
REG_FRF_MID = 0x07
REG_FRF_LSB = 0x08
REG_PA_CONFIG = 0x09
REG_OCP = 0x0B
REG_LNA = 0x0C
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_FIFO_RX_BASE_ADDR = 0x0F
REG_FIFO_RX_CURRENT_ADDR = 0x10
REG_IRQ_FLAGS = 0x12
REG_RX_NB_BYTES = 0x13
REG_PKT_SNR_VALUE = 0x19
REG_PKT_RSSI_VALUE = 0x1A
REG_RSSI_VALUE = 0x1B
REG_MODEM_CONFIG_1 = 0x1D
REG_MODEM_CONFIG_2 = 0x1E
REG_PREAMBLE_MSB = 0x20
REG_PREAMBLE_LSB = 0x21
REG_PAYLOAD_LENGTH = 0x22
REG_MODEM_CONFIG_3 = 0x26
REG_DETECTION_OPTIMIZE = 0x31
REG_DETECTION_THRESHOLD = 0x37
REG_SYNC_WORD = 0x39
REG_DIO_MAPPING_1 = 0x40
REG_VERSION = 0x42
REG_PA_DAC = 0x4D

# ---- modes ---------------------------------------------------------------

MODE_LONG_RANGE = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_TX = 0x03
MODE_RX_CONTINUOUS = 0x05

# ---- IRQ flags -----------------------------------------------------------

IRQ_TX_DONE = 0x08
IRQ_PAYLOAD_CRC_ERROR = 0x20
IRQ_RX_DONE = 0x40

PA_BOOST = 0x80
FXOSC = 32_000_000.0
FSTEP = FXOSC / (1 << 19)

EXPECTED_VERSION = 0x12
"""REG_VERSION on every SX1276/77/78/79.  Anything else means the SPI wiring
is wrong -- 0x00 or 0xFF almost always means MISO or the chip select."""

BANDWIDTHS = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]


class SX127xError(RuntimeError):
    pass


class SX127xRadio(Radio):
    """A real LoRa transceiver on the Pi's SPI bus."""

    def __init__(
        self,
        profile: RadioProfile,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 25,
        dio0_pin: int = 24,
        spi_speed_hz: int = 500_000,
    ) -> None:
        super().__init__(profile)
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.reset_pin = reset_pin
        self.dio0_pin = dio0_pin
        self.spi_speed_hz = spi_speed_hz

        self._spi = None
        self._reset = None
        self._dio0 = None
        self._lock = threading.RLock()
        self._in_tx = False
        self._started = False

    # ---- SPI primitives -------------------------------------------------

    def _read(self, address: int) -> int:
        with self._lock:
            return self._spi.xfer2([address & 0x7F, 0x00])[1]

    def _write(self, address: int, value: int) -> None:
        with self._lock:
            self._spi.xfer2([address | 0x80, value & 0xFF])

    def _read_burst(self, address: int, length: int) -> bytes:
        with self._lock:
            result = self._spi.xfer2([address & 0x7F] + [0x00] * length)
        return bytes(result[1:])

    def _write_burst(self, address: int, data: bytes) -> None:
        with self._lock:
            self._spi.xfer2([address | 0x80] + list(data))

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        try:
            import spidev
            from gpiozero import DigitalInputDevice, DigitalOutputDevice
        except ImportError as exc:  # pragma: no cover - hardware only
            raise SX127xError(
                "spidev and gpiozero are required for hardware use: "
                "pip install spidev gpiozero lgpio"
            ) from exc

        self._reset = DigitalOutputDevice(self.reset_pin, initial_value=True)
        self._spi = spidev.SpiDev()
        self._spi.open(self.spi_bus, self.spi_device)
        self._spi.max_speed_hz = self.spi_speed_hz
        self._spi.mode = 0

        self._hard_reset()

        version = self._read(REG_VERSION)
        if version != EXPECTED_VERSION:
            self.stop()
            raise SX127xError(
                f"SX127x not found: REG_VERSION=0x{version:02X}, expected "
                f"0x{EXPECTED_VERSION:02X}. Check SPI is enabled and MISO/NSS wiring."
            )

        self._configure()

        self._dio0 = DigitalInputDevice(self.dio0_pin, pull_up=False)
        self._dio0.when_activated = self._on_dio0

        self._listen()
        self._started = True
        log.info(
            "SX127x ready: %.3f MHz SF%d BW%dk CR4/%d %d dBm (%s)",
            self.profile.frequency_hz / 1e6,
            self.profile.spreading_factor,
            self.profile.bandwidth_hz // 1000,
            self.profile.coding_rate,
            self.profile.tx_power_dbm,
            self.profile.name,
        )

    def stop(self) -> None:
        self._started = False
        if self._dio0 is not None:
            self._dio0.when_activated = None
            self._dio0.close()
            self._dio0 = None
        if self._spi is not None:
            try:
                self._set_mode(MODE_SLEEP)
            except Exception:
                pass
            self._spi.close()
            self._spi = None
        if self._reset is not None:
            self._reset.close()
            self._reset = None

    def _hard_reset(self) -> None:
        self._reset.off()
        time.sleep(0.01)
        self._reset.on()
        time.sleep(0.01)

    # ---- configuration --------------------------------------------------

    def _set_mode(self, mode: int) -> None:
        self._write(REG_OP_MODE, MODE_LONG_RANGE | mode)

    def _configure(self) -> None:
        p = self.profile

        # LongRangeMode can only be changed while in sleep.
        self._set_mode(MODE_SLEEP)
        time.sleep(0.01)
        self._set_mode(MODE_STDBY)

        self._set_frequency(p.frequency_hz)

        self._write(REG_FIFO_TX_BASE_ADDR, 0x00)
        self._write(REG_FIFO_RX_BASE_ADDR, 0x00)

        self._write(REG_LNA, self._read(REG_LNA) | 0x03)  # LNA boost on

        self._set_bandwidth_cr_header(p)
        self._set_spreading_factor(p.spreading_factor, p.crc)
        self._set_modem_config_3(p)
        self._set_tx_power(p.tx_power_dbm)

        self._write(REG_PREAMBLE_MSB, (p.preamble_length >> 8) & 0xFF)
        self._write(REG_PREAMBLE_LSB, p.preamble_length & 0xFF)
        self._write(REG_SYNC_WORD, p.sync_word)

        self._set_mode(MODE_STDBY)

    def _set_frequency(self, hz: int) -> None:
        frf = int(round(hz / FSTEP))
        self._write(REG_FRF_MSB, (frf >> 16) & 0xFF)
        self._write(REG_FRF_MID, (frf >> 8) & 0xFF)
        self._write(REG_FRF_LSB, frf & 0xFF)

    def _set_bandwidth_cr_header(self, p: RadioProfile) -> None:
        try:
            bw_index = BANDWIDTHS.index(p.bandwidth_hz)
        except ValueError as exc:
            raise SX127xError(f"unsupported bandwidth {p.bandwidth_hz} Hz") from exc
        if not 5 <= p.coding_rate <= 8:
            raise SX127xError(f"coding rate must be 5..8, got {p.coding_rate}")
        value = (bw_index << 4) | ((p.coding_rate - 4) << 1)
        if not p.explicit_header:
            value |= 0x01
        self._write(REG_MODEM_CONFIG_1, value)

    def _set_spreading_factor(self, sf: int, crc: bool) -> None:
        if not 7 <= sf <= 12:
            raise SX127xError(f"spreading factor must be 7..12, got {sf}")
        # Detection thresholds differ for SF6; we do not support it.
        self._write(REG_DETECTION_OPTIMIZE, 0xC3)
        self._write(REG_DETECTION_THRESHOLD, 0x0A)
        value = (sf << 4) | (0x04 if crc else 0x00)
        # Preserve the SymbTimeout MSBs in the low two bits.
        value |= self._read(REG_MODEM_CONFIG_2) & 0x03
        self._write(REG_MODEM_CONFIG_2, value)

    def _set_modem_config_3(self, p: RadioProfile) -> None:
        # bit 3 LowDataRateOptimize, bit 2 AgcAutoOn
        value = 0x04
        if p.low_data_rate_optimize:
            value |= 0x08
        self._write(REG_MODEM_CONFIG_3, value)

    def _set_tx_power(self, dbm: int) -> None:
        """Configure PA_BOOST output -- the RA-02's antenna is wired to it."""
        dbm = max(2, min(20, int(dbm)))
        if dbm > 17:
            self._write(REG_PA_DAC, 0x87)  # +20 dBm mode
            self._write(REG_OCP, 0x3F)  # raise current limit to 240 mA
            self._write(REG_PA_CONFIG, PA_BOOST | (17 - 2))
        else:
            self._write(REG_PA_DAC, 0x84)
            self._write(REG_PA_CONFIG, PA_BOOST | (dbm - 2))

    # ---- receive --------------------------------------------------------

    def _listen(self) -> None:
        self._write(REG_DIO_MAPPING_1, 0x00)  # DIO0 = RxDone
        self._set_mode(MODE_RX_CONTINUOUS)

    def _on_dio0(self) -> None:
        # During a transmit DIO0 signals TxDone, which send() polls for.
        if self._in_tx or not self._started:
            return
        try:
            self._read_packet()
        except Exception:  # pragma: no cover - hardware only
            log.exception("error handling RxDone")

    def _read_packet(self) -> None:
        flags = self._read(REG_IRQ_FLAGS)
        self._write(REG_IRQ_FLAGS, 0xFF)  # clear all

        if not flags & IRQ_RX_DONE:
            return
        if flags & IRQ_PAYLOAD_CRC_ERROR:
            log.debug("dropped frame with CRC error")
            return

        length = self._read(REG_RX_NB_BYTES)
        self._write(REG_FIFO_ADDR_PTR, self._read(REG_FIFO_RX_CURRENT_ADDR))
        data = self._read_burst(REG_FIFO, length)

        self._deliver(data, RxInfo(rssi_dbm=self.packet_rssi(), snr_db=self.packet_snr()))

    def packet_snr(self) -> float:
        raw = self._read(REG_PKT_SNR_VALUE)
        if raw > 127:
            raw -= 256  # register is a signed byte
        return raw / 4.0

    def packet_rssi(self) -> float:
        """Last packet's RSSI in dBm.

        The offset depends on which band the part is tuned to: -164 for the
        low-frequency port (our 433 MHz RA-02), -157 for the high-frequency
        one.  Below 0 dB SNR the reading has to be corrected by the SNR, or
        weak packets read implausibly strong.
        """
        offset = 164 if self.profile.frequency_hz < 779_000_000 else 157
        rssi = self._read(REG_PKT_RSSI_VALUE) - offset
        snr = self.packet_snr()
        if snr < 0:
            rssi += snr
        return float(rssi)

    CARRIER_SENSE_MARGIN_DB = 6.0
    """How far above sensitivity the channel must read to count as busy."""

    def channel_busy(self) -> bool:
        """Instantaneous RSSI check on the current channel.

        Deliberately RSSI rather than the chip's Channel Activity Detection:
        CAD only fires on a LoRa *preamble*, so it cannot see a transmission
        that is already mid-payload -- which is exactly the case we need to
        catch, since a relay's backoff typically expires while another relay
        is partway through its frame.
        """
        if not self._started or self._in_tx:
            return False
        offset = 164 if self.profile.frequency_hz < 779_000_000 else 157
        rssi = self._read(REG_RSSI_VALUE) - offset
        return rssi > self.profile.sensitivity_dbm() + self.CARRIER_SENSE_MARGIN_DB

    # ---- transmit -------------------------------------------------------

    def send(self, data: bytes) -> None:
        if not self._started:
            raise SX127xError("radio not started")
        if len(data) > 255:
            raise SX127xError(f"frame too long: {len(data)} bytes")

        expected_ms = self.profile.airtime_ms(len(data))
        timeout = time.monotonic() + (expected_ms / 1000.0) * 2 + 1.0

        with self._lock:
            self._in_tx = True
            try:
                self._set_mode(MODE_STDBY)
                self._write(REG_DIO_MAPPING_1, 0x40)  # DIO0 = TxDone
                self._write(REG_IRQ_FLAGS, 0xFF)
                self._write(REG_FIFO_ADDR_PTR, 0x00)
                self._write_burst(REG_FIFO, data)
                self._write(REG_PAYLOAD_LENGTH, len(data))
                self._set_mode(MODE_TX)

                # Poll rather than wait on DIO0: airtime is known and
                # bounded, and polling keeps the interrupt path dedicated
                # to RxDone, which is where correctness actually matters.
                while not self._read(REG_IRQ_FLAGS) & IRQ_TX_DONE:
                    if time.monotonic() > timeout:
                        raise SX127xError(
                            f"transmit timed out after {expected_ms:.0f} ms expected airtime"
                        )
                    time.sleep(0.002)

                self._write(REG_IRQ_FLAGS, 0xFF)
            finally:
                self._in_tx = False
                self._listen()
