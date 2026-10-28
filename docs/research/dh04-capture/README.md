# DH04 BLE capture (2026-10-28)

Raw read-only capture from the WattCycle 314Ah Super Mini (DH04SC02-200A, 12 V, 4 cells), for the
wayfinder map "Per-cell BMS data on battery". Three samples at rest, ~2 s apart, of JBD registers
`0x03` (general), `0x04` (cell voltages) and `0x05` (hardware name). `capture.jsonl` is the
output; `capture.py` is the standalone script (read commands only; the live driver must be
stopped first, the BMS link takes one notify client).

## What the pack answered

| Item                     | Observed                                                                                                                                                                                                    |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0x04`                   | Answered. Payload is 2 bytes per cell, big-endian millivolts, one entry per cell: `0d03 0d06 0d05 0d05` = 3331, 3334, 3333, 3333 mV. Their sum (13.331 V) matches the `0x03` pack voltage (1333 = 13.33 V). |
| `0x03` cell/probe counts | `cell_count` 4, `temp_count` 4. All four probes read (13.85, 13.65, 13.75, 14.15 degC); there is no separate MOSFET probe and no absent probe on this pack.                                                 |
| `0x03` balance words     | `0x0000` and `0x0000` in all samples: nothing was balancing (cells within 3 mV). Bit layout not exercised.                                                                                                  |
| `0x03` protection word   | `0x0000` in all samples: no protection active. Bit meanings not exercised.                                                                                                                                  |
| `0x03` FET byte          | `0x03` (`00000011`) in all samples: both FETs on (charge and discharge).                                                                                                                                    |
| `0x03` length            | 47 bytes: 23 fixed + 4 temperature words (8 bytes) + 16 trailing bytes the driver does not decode (they repeat the capacity `0x7aa8` and remaining `0x5ae1` words; meaning unconfirmed).                    |
| `0x05`                   | ASCII `DH04SC02-200A`.                                                                                                                                                                                      |

## Not verified

Balance bit order, protection bit meanings and FET bit meanings were only read as zeros/`0x03`;
they come from the JBD protocol as implemented by `dbus-serialbattery`'s `lltjbd`, not from a
balancing or protection event on this pack.
