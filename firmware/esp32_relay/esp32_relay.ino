/*
  SAR mesh -- ESP32 relay node.

  Standalone flood-mesh relay: hears a packet not addressed to itself,
  waits a random jitter delay (so two relays that both heard the same
  packet don't retransmit at the same instant), then rebroadcasts it with
  TTL decremented, HOP incremented, and LAST_HOP rewritten to this node's
  own address. No routing table, no neighbour discovery -- this mirrors
  sar/mesh.py's MeshNode exactly (same file, "Flood-and-deduplicate mesh
  over LoRa"), just reimplemented in C++ so it can run untethered in the
  field instead of needing a Pi.

  Wire format (packet.py's header, byte-for-byte -- see that file's own
  docstring, which already names this firmware and says to keep both in
  sync if the format ever changes):
      byte 0   VERSION(4b) | MSG_TYPE(4b)
      byte 1   SRC
      byte 2   DST
      byte 3   TTL(4b) | HOP(4b)
      byte 4-5 SEQ (little-endian uint16)
      byte 6   LAST_HOP
      byte 7   PAYLOAD_LENGTH
      byte 8+  PAYLOAD (opaque here -- a relay never needs to decode it,
               only forward it unchanged)

  Radio config matches sar/config.py's FIELD profile exactly -- this is
  what the drone's direct-SPI uplink and the GCS ESP32 already run;
  mismatch on any of these and this relay will never hear or be heard by
  either of them:
      433 MHz, 125 kHz BW, SF10, coding rate 4/5, preamble 8,
      sync word 0x12 (private -- 0x34 is reserved for LoRaWAN),
      explicit header, CRC on, 2 dBm TX power (India's 433 MHz
      license-exempt limit -- see sar/config.py's LEGAL_TX_POWER_DBM_INDIA_433
      before ever raising this).

  Library: arduino-LoRa (Sandeep Mistry) -- install via Arduino IDE's
  Library Manager, search "LoRa". Board: any ESP32 WROOM-32 DevKit.

  Wiring (RA-02 -> ESP32, matches this project's other nodes):
      VCC   -> 3.3V   (NOT 5V -- the module is 3.3V only)
      GND   -> GND
      MISO  -> GPIO19
      MOSI  -> GPIO23
      SCK   -> GPIO18
      NSS   -> GPIO5
      RESET -> GPIO14
      DIO0  -> GPIO26

  Status LEDs (each: GPIO -> 220-330ohm resistor -> LED anode(+);
  LED cathode(-) -> GND. Without the resistor you can damage the LED or
  overdrive the GPIO pin):
      RED   (RX) -> GPIO25 -- flashes ~80ms every time a frame is heard
                              over the air, relayed or not
      GREEN (TX) -> GPIO27 -- lit for the actual duration of this node's
                              own transmission (on right before the radio
                              starts sending, off when it's done -- not a
                              fixed guess, the real airtime)
  GPIO25/27 were picked because they're free on every common WROOM-32
  variant (not shared with flash/PSRAM, not a strapping pin, not already
  used above) -- if you rewire to different pins, update LED_RX/LED_TX
  below to match.
*/

#include <SPI.h>
#include <LoRa.h>
#include <string.h>
#include <esp_system.h> // esp_random()

// ---- this node's identity ------------------------------------------------
// 0x10-0x7F is the relay-node range (see sar/config.py's addressing table).
// Change this if you deploy more than one relay -- every node on the mesh
// needs a unique address, and the drone/GCS already own 0x01-0x02/0xF0.
#define MY_ADDR 0x10 // NODE-01

// ---- radio pins -----------------------------------------------------------
#define PIN_NSS 5
#define PIN_RST 14
#define PIN_DIO0 26

// ---- status LEDs --------------------------------------------------------
// See the header comment for wiring. Cosmetic only -- not needed for
// relaying to work, but useful for field debugging without a laptop
// attached: RED flashes on every frame heard, GREEN is lit for the actual
// duration of this node's own transmissions.
#define LED_RX 25
#define LED_TX 27

// ---- radio profile (sar/config.py FIELD -- must match the rest of the mesh)
#define LORA_FREQ_HZ 433E6
#define LORA_BW_HZ 125E3
#define LORA_SF 10
#define LORA_CR_DENOM 5 // 4/5
#define LORA_PREAMBLE 8
#define LORA_SYNC_WORD 0x12
#define LORA_TX_POWER_DBM 2 // legal limit on 433 MHz in India -- see sar/config.py

// ---- packet header (packet.py) --------------------------------------------
#define HEADER_SIZE 8
#define MAX_PACKET_SIZE 255
#define PROTOCOL_VERSION 1
#define ADDR_BROADCAST 0xFF
#define ADDR_UNASSIGNED 0x00

// ---- duplicate suppression -------------------------------------------------
// Mirrors sar/mesh.py's SeenCache: bounded, time-expiring (src,seq) set.
// 180s retention / 64 entries is plenty for this mesh's packet rate (a
// relay forwards at most a few packets/minute per FIELD profile's own duty
// cycle budget -- see packets_per_minute() in sar/config.py).
#define SEEN_CACHE_SIZE 64
#define SEEN_RETENTION_MS 180000UL

struct SeenEntry {
  uint8_t src;
  uint16_t seq;
  unsigned long at;
  bool used;
};
SeenEntry seenCache[SEEN_CACHE_SIZE];

bool seenCheckAndAdd(uint8_t src, uint16_t seq) {
  unsigned long now = millis();
  int oldestIdx = 0;
  unsigned long oldestAt = now;
  for (int i = 0; i < SEEN_CACHE_SIZE; i++) {
    if (seenCache[i].used && (now - seenCache[i].at) > SEEN_RETENTION_MS) {
      seenCache[i].used = false; // expired
    }
    if (seenCache[i].used && seenCache[i].src == src && seenCache[i].seq == seq) {
      return false; // duplicate
    }
    if (!seenCache[i].used) {
      oldestIdx = i;
      oldestAt = 0;
    } else if (seenCache[i].at < oldestAt) {
      oldestIdx = i;
      oldestAt = seenCache[i].at;
    }
  }
  seenCache[oldestIdx] = {src, seq, now, true};
  return true; // new
}

// ---- stats (surfaced over Serial for field debugging) ----------------------
unsigned long statsHeard = 0;
unsigned long statsForwarded = 0;
unsigned long statsDuplicates = 0;
unsigned long statsTtlExpired = 0;

// ---- airtime, for the jitter window (mirrors RadioProfile.airtime_ms) ------
// Same formula as sar/config.py -- see that method's own docstring for the
// SX1276 datasheet reference. Needed here for the same reason it's needed
// there: the jitter window has to scale with airtime, or it's either too
// short to prevent collisions (at high SF) or needlessly slow (at low SF).
float airtimeMs(int payloadBytes) {
  float sf = LORA_SF;
  float bw = LORA_BW_HZ;
  float tSym = pow(2, sf) / bw;
  float tPreamble = (LORA_PREAMBLE + 4.25) * tSym;
  bool lowDataRateOptimize = (pow(2, sf) / bw) > 0.016;
  int de = lowDataRateOptimize ? 1 : 0;
  int ih = 0; // explicit header
  int crc = 1;
  float numerator = 8 * payloadBytes - 4 * sf + 28 + 16 * crc - 20 * ih;
  float denominator = 4 * (sf - 2 * de);
  float nPayload = 8 + max(ceil(numerator / denominator) * LORA_CR_DENOM, 0.0f);
  return (tPreamble + nPayload * tSym) * 1000.0;
}

// ---- forwarding queue -------------------------------------------------------
// One in-flight forward at a time is enough here: FIELD profile's own duty
// cycle budget (packets_per_minute in sar/config.py) means real traffic is
// sparse -- a relay is not expected to need to hold more than one packet's
// jitter delay at once. If that ever changes, this is the place to widen it.
uint8_t pendingFrame[MAX_PACKET_SIZE];
int pendingLen = 0;
unsigned long pendingSendAt = 0;
bool pendingValid = false;

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("[relay] SAR mesh relay starting...");
  Serial.print("[relay] address: 0x");
  Serial.println(MY_ADDR, HEX);

  for (int i = 0; i < SEEN_CACHE_SIZE; i++) seenCache[i].used = false;
  randomSeed(esp_random()); // hardware RNG -- jitter needs real variation, not the same sequence every boot

  LoRa.setPins(PIN_NSS, PIN_RST, PIN_DIO0);
  if (!LoRa.begin(LORA_FREQ_HZ)) {
    Serial.println("[relay] LoRa init FAILED -- check wiring (see header comment)");
    while (true) delay(1000);
  }
  LoRa.setSignalBandwidth(LORA_BW_HZ);
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setCodingRate4(LORA_CR_DENOM);
  LoRa.setPreambleLength(LORA_PREAMBLE);
  LoRa.setSyncWord(LORA_SYNC_WORD);
  LoRa.setTxPower(LORA_TX_POWER_DBM, PA_OUTPUT_PA_BOOST_PIN);
  LoRa.enableCrc();

  pinMode(LED_RX, OUTPUT);
  pinMode(LED_TX, OUTPUT);
  digitalWrite(LED_RX, LOW);
  digitalWrite(LED_TX, LOW);

  Serial.println("[relay] ready: 433.0 MHz SF10 BW125k CR4/5");
}

void flashRx() {
  // Fixed, brief flash -- "heard a frame" has no natural duration the way
  // a transmission does (see the TX LED below), so this is just long
  // enough for a human eye to catch it.
  digitalWrite(LED_RX, HIGH);
  delay(80);
  digitalWrite(LED_RX, LOW);
}

void loop() {
  // 1. Service any pending forward whose jitter delay has elapsed.
  if (pendingValid && millis() >= pendingSendAt) {
    // Lit for the real transmission window, not a guessed duration:
    // endPacket() blocks until the radio has actually finished sending.
    digitalWrite(LED_TX, HIGH);
    LoRa.beginPacket();
    LoRa.write(pendingFrame, pendingLen);
    LoRa.endPacket();
    digitalWrite(LED_TX, LOW);
    pendingValid = false;
    statsForwarded++;
    Serial.print("[relay] forwarded, ");
    Serial.print(pendingLen);
    Serial.println(" bytes");
  }

  // 2. Check for a newly-received frame.
  int packetSize = LoRa.parsePacket();
  if (packetSize == 0) return;
  if (packetSize < HEADER_SIZE || packetSize > MAX_PACKET_SIZE) {
    while (LoRa.available()) LoRa.read(); // drain the runt/oversized frame
    return;
  }

  uint8_t frame[MAX_PACKET_SIZE];
  for (int i = 0; i < packetSize; i++) frame[i] = LoRa.read();
  statsHeard++;

  // ---- parse header (packet.py's exact layout) -----------------------
  uint8_t versionType = frame[0];
  uint8_t version = versionType >> 4;
  uint8_t src = frame[1];
  uint8_t dst = frame[2];
  uint8_t ttlHop = frame[3];
  uint8_t ttl = ttlHop >> 4;
  uint8_t hop = ttlHop & 0x0F;
  uint16_t seq = frame[4] | (frame[5] << 8);
  uint8_t payloadLen = frame[7];

  if (version != PROTOCOL_VERSION) return; // unknown protocol version -- not ours to touch
  if ((HEADER_SIZE + payloadLen) > packetSize) return; // truncated frame

  flashRx(); // a validly-framed packet, not just RF noise that tripped parsePacket()

  float rssi = LoRa.packetRssi();
  float snr = LoRa.packetSnr();
  Serial.print("[relay] heard src=0x");
  Serial.print(src, HEX);
  Serial.print(" dst=0x");
  Serial.print(dst, HEX);
  Serial.print(" seq=");
  Serial.print(seq);
  Serial.print(" ttl=");
  Serial.print(ttl);
  Serial.print(" hop=");
  Serial.print(hop);
  Serial.print(" rssi=");
  Serial.print(rssi);
  Serial.print(" snr=");
  Serial.println(snr);

  // ---- relay decision (mirrors MeshNode._handle_frame) -----------------
  if (src == MY_ADDR) return; // our own packet, echoed back by another relay

  if (!seenCheckAndAdd(src, seq)) {
    statsDuplicates++;
    Serial.println("[relay]   duplicate, dropped");
    return;
  }

  if (dst == MY_ADDR) {
    Serial.println("[relay]   addressed to us -- terminal, not relayed");
    return; // arrived; broadcasts still fall through below
  }

  if (ttl <= 1) {
    statsTtlExpired++;
    Serial.println("[relay]   ttl exhausted, not relayed");
    return;
  }

  // ---- build the forwarded copy: ttl-1, hop+1, last_hop = us -----------
  // SRC, DST, SEQ, and the payload are never touched -- only a relay's own
  // hop-local fields change (see Packet.for_forwarding in packet.py).
  memcpy(pendingFrame, frame, packetSize);
  pendingFrame[3] = ((ttl - 1) << 4) | min(hop + 1, 15);
  pendingFrame[6] = MY_ADDR; // LAST_HOP
  pendingLen = packetSize;

  // airtime_ms() takes the FULL frame size (header+payload) -- matches
  // Packet.size_bytes in packet.py, which is what sar/mesh.py's
  // _forward_delay is actually called with, not just the payload length.
  float airtime = airtimeMs(packetSize);
  float low = airtime * 0.25;
  float high = airtime * 6.0; // JITTER_AIRTIME_MULTIPLE, see sar/mesh.py
  long jitterMs = random((long)low, (long)high + 1);
  pendingSendAt = millis() + jitterMs;
  pendingValid = true;

  Serial.print("[relay]   will relay in ");
  Serial.print(jitterMs);
  Serial.println(" ms");
}
