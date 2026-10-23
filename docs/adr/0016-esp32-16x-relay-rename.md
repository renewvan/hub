---
status: accepted
---

# The ESP32 relay Device is `esp32-16x-relay`

The Device that ADR 0012 named `esp32-relay` (repo `driver-esp32-relay` after ADR 0014) is renamed `esp32-16x-relay`. The driver repo's rename commit (`73dc815`) moved the repo, ESPHome project name, firmware YAML, Device key and the `info`/`health` topics to the `16x` name together; this ADR records the hub-side consequences. The motive for the `16x` suffix is not written down in that commit.

| Was                                                | Now                                                        |
| -------------------------------------------------- | ---------------------------------------------------------- |
| Repo `renewvan/driver-esp32-relay`                 | `renewvan/driver-esp32-16x-relay`                          |
| Device key `esp32-relay`                           | `esp32-16x-relay`                                          |
| Retained `renewvan/esp32-relay/info` and `/health` | `renewvan/esp32-16x-relay/info` and `/health`              |
| Firmware file `esp32-relay-driver.yaml`            | `esp32-16x-relay-driver.yaml`                              |
| Product `ESP32 16-channel relay board`             | `ESP32-WROOM-E 16-channel relay board` (the real manifest) |

## Rules this sets

- The rule of ADR 0012 is unchanged: a Device whose hardware is a purchasable relay board is named by its board, and the key is the repo name without the `driver-` prefix (ADR 0014). Only this board's name changes.
- The entity topics `renewvan/relay/<id>/...` carry no Device key, so they do not move and the dashboard's Relays page is unaffected.
- The old key's retained `info` and `health` do not move on their own and must be cleared (ADR 0012's retired-key rule), or the old Device stays `online` on the Device page after its board has been reflashed under the new key. Clear them with an empty retained publish to exactly those two topics.

## Supersedes (naming only)

ADRs 0012, 0013, 0014 and 0015 are historical records and are left as written, so they still say `esp32-relay`. Read them with the table above. Their decisions stand: the board-named exception (0012), the writable relay contract (0013), and the firmware release type (0015). This ADR changes only the name.

## Why this is an ADR

Hard to reverse: the repo name, Device key and the two retained topics are public contracts that other repos and deployments pin by name, and a stale retained topic keeps reporting a dead Device `online`. Surprising without context: the accepted ADRs still say `esp32-relay` while the code says `esp32-16x-relay`.

## Cutover

- The board that was flashed under the old key is the same physical board (same `board_serial`); reflashing it publishes under the new key. No compatibility shim: there are no external adopters.
- On the van this repo's author runs, the old key's two retained topics were cleared on the broker when the new key came up.
- `.scratch/` planning notes still use the old name and are not rewritten.

## See also

- `docs/adr/0012-board-named-relay-node.md`, `docs/adr/0014-node-to-device-rename.md` — the naming decisions this one amends.
- `CONTEXT.md` — **Naming convention** and **Device artifact naming**.
