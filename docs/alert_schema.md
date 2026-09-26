# Alert Schema: Drone → Rescue Team

> **Status:** 🔧 Specification. This defines the message the drone sends when it detects a
> possible survivor or hazard. The implementation must match this document; update both together.

Alerts go **directly from the drone to the rescue team**, with no cloud server in between.
The drone tries Wi-Fi first and falls back to LoRa automatically.

```
Detection ─► Confirm (N frames) ─► Wi-Fi alert ──(ACK within timeout?)──► done
                                        │ no
                                        ▼
                                   LoRa alert ─► ESP32 receiver ─► Flutter app
```

## 1. Fields

| Field | Type | Example | Notes |
|---|---|---|---|
| `v` | int | `1` | Schema version |
| `id` | int | `17` | Alert ID, increments per drone |
| `drone` | string | `A1` | Drone ID |
| `ts` | int | `1790354954` | Unix time (UTC), from GPS |
| `cls` | string | `occluded_person` | `person`, `occluded_person`, `fire`, `smoke`, `crack` |
| `conf` | float | `0.82` | Detection confidence, 0–1 |
| `lat`, `lon` | float | `12.971612`, `77.594566` | See *Location* below |
| `loc_src` | string | `drone` | `drone` = drone's own GPS; `projected` = survivor position estimate (planned) |
| `pri` | string | `CRITICAL` | `CRITICAL` / `HIGH` / `MEDIUM` (planned, see §4) |
| `link` | string | `wifi` | Which link delivered it: `wifi` or `lora` |
| `img` | JPEG | *(Wi-Fi only)* | Cropped detection snapshot. No face recognition. |

### Location

Today `lat/lon` is the **drone's** GPS position at detection time (`loc_src: drone`).
Estimating the survivor's own position is planned (`loc_src: projected`); see
[`architecture.md`](architecture.md#survivor-location).

## 2. Wi-Fi alert (full)

Sent as JSON, with the cropped snapshot attached:

```json
{
  "v": 1,
  "id": 17,
  "drone": "A1",
  "ts": 1790354954,
  "cls": "occluded_person",
  "conf": 0.82,
  "lat": 12.971612,
  "lon": 77.594566,
  "loc_src": "drone",
  "pri": "CRITICAL",
  "link": "wifi"
}
```
+ `snapshot_17.jpg` (detection crop)

## 3. LoRa alert (compact)

A LoRa packet (SX1278) is limited to 255 bytes and is slow, so it carries **text only, no image**.
Comma-separated, in this fixed order:

```
v,id,drone,ts,cls,conf,lat,lon,loc_src,pri
```

Example (68 bytes):

```
1,17,A1,1790354954,occluded_person,0.82,12.971612,77.594566,drone,CRITICAL
```

The ESP32 receiver adds `link=lora` and forwards it to the Flutter app.

## 4. Priority (planned)

A START/SALT-inspired score from 0–100, used to rank alerts on the rescuer's screen:

| Condition | Points |
|---|---|
| Occluded (partly buried) person | +30 (visible person: +20) |
| Fire nearby | +25 |
| Smoke nearby | +15 |
| Structural crack nearby | +10 |
| Detection confidence | + conf × 20 |

`≥ 70` → CRITICAL · `≥ 45` → HIGH · otherwise MEDIUM. Capped at 100.

The score is a **decision aid for rescuers, not a medical triage**. A human verifies every alert.

## 5. Delivery rules

- **Confirm before alerting:** a detection must persist across N consecutive frames
  (N to be tuned) to reduce false alerts.
- **Fallback:** if the Wi-Fi alert is not acknowledged within a timeout, resend over LoRa.
- **No duplicates:** the same `id` is never sent twice on the same link.
- **Privacy:** snapshots are detection crops; no face recognition or identity data.

## 6. Metrics to report

Not yet measured. These are the numbers the alert link should be judged on:

| Metric | How to measure |
|---|---|
| End-to-end latency | Detection timestamp → app receipt, over Wi-Fi and over LoRa |
| Delivery rate | Alerts received ÷ alerts sent, at set distances |
| LoRa range | Distance at which delivery rate drops below 90% |
