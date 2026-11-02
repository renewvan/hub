# VE.Direct text-protocol facts the mapping depends on

Research for wayfinder ticket renewvan/hub#108 (parent map #106, blocks #113). Researched 2026-11-02.

## Sources

| Tag | Source                                                                                                                                                                                                            |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P   | Victron, "VE.Direct Protocol" v3.34, 14 April 2025. <https://www.victronenergy.com/upload/documents/VE.Direct-Protocol-3.34.pdf>. Cited by section name (Table I = "Supported Text-mode fields").                 |
| F   | Victron, "VE.Direct Protocol FAQ". <https://www.victronenergy.com/live/vedirect_protocol:faq>. Cited by question number (Q7, Q8, and "Framehandler reference implementation").                                    |
| D   | Victron, Venus OS D-Bus wiki. <https://github.com/victronenergy/venus/wiki/dbus> (raw: <https://raw.githubusercontent.com/wiki/victronenergy/venus/dbus.md>). Cited by section.                                   |
| G   | Victron, gui-v2 `src/productinfo.h`. <https://github.com/victronenergy/gui-v2/blob/main/src/productinfo.h>.                                                                                                       |
| V   | renewvan/driver-venus at `ac27cca`: `driver_venus/mapping.py` and `README.md`.                                                                                                                                    |
| C   | Raw captures on branch `origin/research/vedirect-capture`, dir `research/vedirect-capture/` (ticket #107), 2026-11-02, about 60 s each. Parsed with a checksum-validating parser; every frame passed (see below). |

"Unverified" means not found in P, F, D or G. Anything seen only in C is marked "capture only".

The text extracted from P loses the per-device bullet columns of Table I (which device supports which field). Per-device field support below therefore comes from P's footnotes, P's changelog and C, and is flagged where it rests on C alone.

## 1. Product IDs (PID)

| Device                                          | PID in P                                                   | PID in capture | Verdict                                                                                                                                                                  |
| ----------------------------------------------- | ---------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| SmartSolar MPPT 100\|30                         | `0xA056` (P, "PID")                                        | `0xA056`       | Match.                                                                                                                                                                   |
| Orion XS 12V/12V-50A                            | `0xA3F0` (P, "PID"; renamed from Smart BuckBoost in v3.34) | `0xA3F0`       | Match. Orion XS 1400 is `0xA3F1`. G defines the whole Orion XS family as `0xA3F0..0xA3FF` (`ProductId_OrionXs_Min/Max`), so match a range, not one value.                |
| SmartShunt 500A/50mV                            | `0xA389` (P, "PID"). 1000A is `0xA38A`, 2000A is `0xA38B`  | `0xC035`       | **Disagree.** The van's shunt reports `0xC035` and `BMV  SmartShunt 500A/50mV`. `0xC035` is in no primary source I found. Do not key on `0xA389`; unverified.            |
| Blue Smart charger (IP22 family, model unknown) | Not listed in P                                            | `0xA330`       | **Unverified.** P lists only Phoenix Smart IP43 chargers (`0xA340..0xA347`). `0xA330` for a Blue Smart charger comes from the capture only. P names no Blue Smart table. |

Other facts from P: BMV-700/702/700H are `0x203/0x204/0x205`; BMV-712 Smart `0xA381`, BMV-710H Smart `0xA382`, BMV-712 Smart Rev2 `0xA383`; SmartSolar MPPT is `0xA050..0xA081` plus VE.Can `0xA102..0xA117`.

Recommendation for the driver: identify device class by field set (and PID range where documented), not by exact PID. Capture PIDs: Orion XS in range `0xA3F0..0xA3FF` (G), MPPT in `0xA040..0xA081` (P), the shunt by the presence of `SOC`/`CE`/`BMV`, the Blue Smart by `HC#`/absence of `P`.

## 2. State codes

### CS (state of operation), P, "CS"

| Code | Name                        | Applies to (P)          |
| ---- | --------------------------- | ----------------------- |
| 0    | Off                         | MPPT, Inverter, Charger |
| 1    | Low power                   | Inverter (load search)  |
| 2    | Fault                       | MPPT, Inverter, Charger |
| 3    | Bulk                        | MPPT, Charger           |
| 4    | Absorption                  | MPPT, Charger           |
| 5    | Float                       | MPPT, Charger           |
| 6    | Storage                     | Charger                 |
| 7    | Equalize (manual)           | MPPT                    |
| 9    | Inverting                   | Inverter                |
| 11   | Power supply                | Charger                 |
| 245  | Starting-up                 | MPPT                    |
| 246  | Repeated absorption         | Charger                 |
| 247  | Auto equalize / Recondition | MPPT, Charger           |
| 248  | BatterySafe                 | Charger                 |
| 252  | External control            | MPPT                    |

The column assignment is read from the PDF bullets and is the most likely reading; the code names and numbers are certain. D ("Solar chargers" `/State`, "AC chargers" `/State`) uses the same numbers for the D-Bus `State`, which is why driver-venus can reuse them.

Capture: MPPT `CS 3` (bulk), Blue Smart `CS 3` (bulk), Orion XS `CS 0` (off, with `OR 0x00000081`). Nothing in the captures contradicts the table.

### MODE (device mode), P, "MODE"

| Code | Name                  | Applies to            |
| ---- | --------------------- | --------------------- |
| 1    | VE_REG_MODE_CHARGER   | Charger               |
| 2    | VE_REG_MODE_INVERTER  | Inverter              |
| 4    | VE_REG_MODE_OFF       | Inverter, Charger     |
| 5    | VE_REG_MODE_ECO       | Inverter              |
| 253  | VE_REG_MODE_HIBERNATE | Inverter (Smart only) |

P's Table I marks `MODE` for Phoenix Inverter, Phoenix (Smart) Charger and Smart BuckBoost/Orion XS columns only. **None of the four captured devices sends a `MODE` line**, so this table is not needed for the four devices at hand. Do not confuse with `MPPT` (tracker mode: 0 off, 1 voltage/current limited, 2 MPP tracker active, P "MPPT") or `MON` (DC monitor mode, P "MON"; the shunt sends `MON 0` = battery monitor).

### What driver-venus maps (V, `mapping.py` `CHARGER_STATE_CODES` / `SYSTEM_STATE_CODES`, README row for `charger/<n>/State`)

| Venus State code                            | `charge_state`                                            |
| ------------------------------------------- | --------------------------------------------------------- |
| 4, 246                                      | `absorption` (246 = repeated absorption)                  |
| 7, 247                                      | `equalize` (247 = auto equalize/recondition)              |
| 5, 248                                      | `float` (248 = BatterySafe)                               |
| 3 / 6 / 0 / 2 / 252                         | `bulk` / `storage` / `off` / `fault` / `external_control` |
| 11 (power supply), 245, 1, 9, anything else | `unknown` for chargers                                    |

driver-venus has no `MODE` or `OR`/`ERR` mapping (grep of `mapping.py` finds no `MODE`). The VE.Direct `CS` numbers equal the Venus numbers for these codes (compare table above with D), so a native VE.Direct driver can share the same table. Differences to decide in #113: `245` (starting-up) and `11` (power supply) exist on the wire but map to `unknown` in driver-venus.

## 3. Units and sign (P, Table I and "Detailed field description")

| Field          | Unit on wire  | Sign / meaning                                                                                                                                               | Capture check                                                                                                              |
| -------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| V              | mV            | Main (battery) voltage.                                                                                                                                      | `13290` = 13.29 V on all four devices.                                                                                     |
| I              | mA            | "Both for MPPTs and BMVs: when > 0, the battery is being charged, < 0 the battery is being discharged" (P, "I").                                             | Shunt `I 893` while CE falls (charging). MPPT `I 1500`. Blue Smart `I 30000` constant (= 30.000 A, positive). Orion `I 0`. |
| VPV            | mV            | PV panel voltage (MPPT only).                                                                                                                                | `VPV 49200` = 49.2 V.                                                                                                      |
| PPV            | W             | PV panel power (MPPT only).                                                                                                                                  | `PPV 21` while V x I = 13.33 V x 1.5 A = 20 W. Consistent.                                                                 |
| P              | W             | Instantaneous power (BMV/SmartShunt; also Orion XS per the v3.33 changelog). Sign follows I (inferred; P does not state it).                                 | Shunt `P 12` with `I 893` and `V 13323` gives 11.9 W.                                                                      |
| CE             | mAh           | Consumed Amp Hours. `---` if not synchronised or in DC-monitor mode (P footnotes 10, 11). Negative = net charge returned since sync (inferred, see capture). | Shunt `CE -113566` decreases as `I > 0` (charging): -113552 -> -113557 within the capture. Consistent with "consumed".     |
| SOC            | per mille (‰) | State of charge, 0..1000. `---` when unsynchronised (footnotes 10, 11).                                                                                      | `SOC 679` = 67.9 %.                                                                                                        |
| TTG            | minutes       | Time-to-go. "When the battery is not discharging the time-to-go is infinite. This is represented as -1" (footnote 12).                                       | `TTG -1`.                                                                                                                  |
| T              | degrees C     | Battery temperature. "When no temperature sensor is connected, `---` will be sent instead of a value" (footnote 8).                                          | Blue Smart `T ---`.                                                                                                        |
| H9             | seconds       | Number of seconds since last full charge.                                                                                                                    | `H9 693631`, +1 per second across the capture (~8.0 days).                                                                 |
| H20            | 0.01 kWh      | Yield today (MPPT). H19 total yield, H22 yesterday: also 0.01 kWh. H21/H23 max power today/yesterday in W.                                                   | MPPT `H20 14` = 0.14 kWh.                                                                                                  |
| VIN            | n/a           | **No field named `VIN` exists in P.** The input voltage field is `DC_IN_V`, unit 0.01 V (P, Table I, added in v3.33).                                        | Orion `DC_IN_V 1232` = 12.32 V.                                                                                            |
| DC_IN_I        | 0.1 A         | DC input current (Orion XS).                                                                                                                                 | `0`.                                                                                                                       |
| DC_IN_P        | W             | DC input power (Orion XS).                                                                                                                                   | `0`.                                                                                                                       |
| H1, H2, H3, H6 | mAh           | Deepest/last/average discharge depth, cumulative Ah drawn (shunt). Stored negative.                                                                          | `H1 -168382`.                                                                                                              |
| H7, H8         | mV            | Min/max main voltage.                                                                                                                                        | `5975`, `14731`.                                                                                                           |
| H17, H18       | 0.01 kWh      | Discharged / charged energy.                                                                                                                                 | `3091`, `3099`.                                                                                                            |

Notes:

- Different units from one field to the next (mV, mA, 0.01 V, 0.1 A, ‰) are intentional (P, "Fields": "The values ... do not necessarily use the same units as the values on the LCD"). `DC_IN_V` and `DC_IN_I` use different scales from `V` and `I` on the same device.
- `T` has no stated unit on the MPPT/charger side beyond the table's degrees C, and P only lists it for BMV-70x/71x/SmartShunt (with AUX configured as temperature). The Blue Smart charger sends `T` anyway (capture only; documented chargers not covered in P).
- P gives no sign convention for `P`, `PPV`, `CE` beyond what is above. The `P` sign (follows `I`) is inferred from one capture and unverified in a primary source.

## 4. Fields per device

Observed field set per capture (C, all frames identical in key set; value sets in parentheses where informative).

| Device (PID)               | Fields in capture, in wire order                                                                                                                                 |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Orion XS (`0xA3F0`)        | `PID FWE SER# V I P DC_IN_V DC_IN_I DC_IN_P CS OR ERR`                                                                                                           |
| Blue Smart (`0xA330`)      | `PID FWE SER# V I T ERR CS HC#`                                                                                                                                  |
| SmartShunt (`0xC035`)      | Block A: `PID V I P CE SOC TTG Alarm AR BMV FW MON`. Block B: `H1 H2 H3 H4 H5 H6 H7 H8 H9 H10 H11 H12 H15 H16 H17 H18`. No `SER#`, no `Relay`, no `VS`/`VM`/`T`. |
| SmartSolar MPPT (`0xA056`) | `PID FW SER# V I VPV PPV CS MPPT OR ERR LOAD H19 H20 H21 H22 H23 HSDS`                                                                                           |

Answers to the ticket's specific questions:

- **Does Orion XS report power or VIN?** It reports power (`P`, battery side, and `DC_IN_P`, input side) and input voltage as `DC_IN_V` (0.01 V), not `VIN`. P's changelog v3.33 lists exactly "PID, FEW [sic, FWE], SER#, V, I, P, DC_IN_V, DC_IN_I, DC_IN_P, CS, OR, ERR" for the device (then called Smart BuckBoost, renamed Orion XS in v3.34). The capture matches this list plus nothing extra. `T`, `MODE` are not sent by the captured Orion.
- **Do BMV/SmartShunt send alternating or partial frames?** Yes. The shunt sends two different blocks (A: live values, B: history H1..H18), each with its own checksum, alternating strictly (60 A + 60 B in the capture, each valid). P: "Multiple blocks are sent containing different fields" (P, "Data integrity"). A consumer must merge blocks by key and not treat a missing `V` as an error in block B. Fields can also be absent from a block depending on configuration (`T`/`VS`/`VM` need AUX configured, P footnotes 3, 4, 9); the capture has no `T`/`VS`/`VM`, yet it does send `H15`/`H16` (min/max auxiliary voltage, `-8` and `3`), which P footnote 3 says only exist with AUX as starter battery. That is a doc/capture disagreement with no consequence for #113 if H15/H16 are ignored.
- **Other devices**: Orion XS, Blue Smart and MPPT each send one complete block per frame in the capture (no partial frames).
- **Limits**: P recommends label buffer 9 bytes, value buffer 33 bytes, and "maximum number of fields in a block is 22" (P, "Implementation guidelines"). The MPPT block has 18 fields, within the limit.
- **Undocumented in P**: `HC#` (Blue Smart; value `702382`, purpose unknown, capture only) and the `Relay` field for the shunt (absent in capture). `FW` on MPPT is `174` (= v1.74) vs `FWE` on Orion/Blue Smart (`113FF`, `0369FF`; P "FWE": up to 6 digits, `FF` = official release).
- **OR (off reason)** is a bitfield (P, "OR"): 0x1 no input power, 0x2 power switch, 0x4 device mode register, 0x8 remote input, 0x10 protection, 0x20 PAYGO, 0x40 BMS, 0x80 engine shutdown detection, 0x100 analysing input voltage. Orion capture `0x00000081` = no input power + engine shutdown detection (consistent with `DC_IN_V` 12.3 V and `CS 0`). MPPT `0x00000000`.
- **ERR** codes: P, "ERR" table (0 none, 2 battery voltage too high, 17 charger temperature too high, 18 charger over current, ... 119 user settings invalid). All captures show `ERR 0`.

## 5. Cadence

- P, "Message format": "The device transmits blocks of data at 1 second intervals." (per block).
- Captures have no timestamps, so cadence is inferred from frame count over the ~60 s capture:

| Device          | Valid frames      | Inferred cadence                                                                                                                                                    |
| --------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SmartSolar MPPT | 61                | ~1 block/s (agrees with P)                                                                                                                                          |
| Blue Smart      | 59                | ~1 block/s (agrees with P)                                                                                                                                          |
| SmartShunt      | 120 (60 A + 60 B) | ~2 blocks/s total, each block type ~1/s (agrees with P)                                                                                                             |
| Orion XS        | 15                | **~1 block per 4 s. Disagrees with P's 1 s.** Timing is inferred from count over a nominal 60 s, so treat as approximate; P itself does not carve out an exception. |

A driver must therefore not assume 1 Hz per device; use a staleness timeout of at least ~10 s for Orion XS (design choice, not from a source).

## 6. Checksum and HEX interleaving

Rule (P, "Data integrity"; F Q8): the block ends with the line `Checksum<TAB><1 byte>`. "The modulo 256 sum of all bytes in a block will equal 0." F Q8: sum "from the start (0d) till the end of the frame, including the checksum value from the frame", i.e. from the leading `\r` of the first `\r\n<label>` up to and including the checksum byte itself. The checksum byte is binary and may be any value, including `\r`, `\n`, `:` or `\t`.

HEX interleaving:

- P, Introduction: older firmware switches to HEX mode when it receives a HEX message and may send asynchronous HEX messages ("starting with `:A` and ending with a newline `\n`") that "can interrupt a regular Text-mode frame"; newer firmware keeps text mode always on. P does not say which products are which ("check its specific VE.Direct protocol document").
- F Q7: after receiving a HEX message the product stops sending TEXT frames for a few seconds, so a driver that polls with HEX can starve the text stream.
- The official reference state machine (F, "Framehandler reference implementation", `VeDirectFrameHandler::rxData`):
  1. If the byte is `:` and the parser is not in the CHECKSUM state, enter RECORD_HEX.
  2. Add the byte to the running checksum only when not in RECORD_HEX.
  3. In RECORD_HEX, hand bytes to the HEX handler until it reports end of message (`\n`), then return to IDLE; HEX bytes (including the `:` and the final `\n`) are not included in the text checksum.
  4. A record named `CHECKSUM` (case-insensitive after `toupper`) moves to the CHECKSUM state; the next byte is added to the sum, then `sum == 0` decides validity, the sum resets and the parser returns to IDLE.
  5. Because step 1 excludes the CHECKSUM state, a checksum byte equal to `:` is not mistaken for a HEX start.

Capture results (C): every frame in all four files validates under this rule (Orion 15/15, Blue Smart 59/59, shunt 120/120, MPPT 61/61).

| File                | HEX messages | HEX inside a text frame? | Where HEX appears                                         |
| ------------------- | ------------ | ------------------------ | --------------------------------------------------------- |
| orion-xs.raw        | 58           | No                       | Directly after the `Checksum` byte, before the next frame |
| blue-smart.raw      | 204          | No                       | Same                                                      |
| smartsolar-mppt.raw | 2            | No                       | Same                                                      |
| smartshunt.raw      | 0            | n/a                      | n/a                                                       |

In these captures HEX never interrupted a frame, but P allows it, so the parser must implement the full state machine (the ones above), not "split on blank lines". A text parser that does not skip `:...\n` would see `Checksum<byte>:A10EE00FF4E` as a garbled checksum record and mis-sum the frame (the `:A...` async messages appear on the same line as the checksum byte in raw output).

## 7. Summary of disagreements and unverified items

| Item                                                                      | Status                                                                                              |
| ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| SmartShunt PID `0xC035` vs P's `0xA389`                                   | Capture wins; `0xC035` not in any primary source found. Unverified.                                 |
| Blue Smart PID `0xA330`                                                   | Not in P. Unverified.                                                                               |
| Blue Smart fields beyond `V I T ERR CS` (`FWE`, `SER#`, `PID`), and `HC#` | `HC#` undocumented in P. `T` on a charger not described in P. Capture only.                         |
| Orion XS cadence about 4 s vs P's 1 s                                     | Capture only, approximate (no timestamps).                                                          |
| Shunt sends `H15`/`H16` without AUX configured                            | Contradicts P footnote 3; harmless.                                                                 |
| Per-device support columns of Table I                                     | Bullet columns lost in text extraction; field support taken from footnotes, changelog and captures. |
| `P` sign convention, `CE` negative meaning                                | Inferred from one capture; P states only `I` sign and "consumed".                                   |
| Which products use "older" vs "newer" Text/HEX behaviour                  | Not stated in P; captures show HEX only between frames.                                             |
| `MODE` table                                                              | Verified in P, but none of the four devices sends `MODE`; driver-venus has no MODE mapping.         |
