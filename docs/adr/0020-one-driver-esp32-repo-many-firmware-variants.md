---
status: accepted
---

# One `driver-esp32` repo, one Driver `esp32`, many firmware variants

All ESP32 firmware lives in one repo, `renewvan/driver-esp32` (was `driver-esp32-16x-relay`; before that `driver-esp32-relay`). Today it ships one firmware, for the 16-channel relay board. Later it is to hold the others (for example a Bluetooth hub, an OBD2 transceiver, a temperature and humidity sensor).

The repo is **one Driver, keyed `esp32`**: its repo name minus `driver-`, like every other repo (ADR 0014, ADR 0018). Firmware **variants** only select which firmware is built and flashed; they are not Drivers and have no key of their own. The variant name `esp32-16x-relay` survives as the firmware and manifest file name, the ESPHome node name and the ESPHome project name, and nowhere on the bus. The relay firmware's retained topics move to `renewvan/esp32/info` and `renewvan/esp32/health`, and each relay's retained `renewvan/relay/<id>/driver` is `"esp32"`. Entity topics and entity ids do not move.

| Was                                                                   | Now                                                                              |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Repo `driver-esp32-16x-relay`, image-less firmware repo (ADR 0015)    | `driver-esp32`, still a `firmware` release, one tag for every variant            |
| A Driver's key is its repo name minus `driver-` (ADR 0014, ADR 0012)  | Still true, with no exception: the repo is one Driver, `esp32`                   |
| A Driver whose hardware is a purchasable board is named by that board | The Driver is named for its repo; a variant is only a firmware, not a Driver key |
| Driver key `esp32-16x-relay`                                          | `esp32`                                                                          |
| Retained `renewvan/esp32-16x-relay/info` and `/health` (ADR 0016)     | `renewvan/esp32/info` and `/health`                                              |
| Retained `renewvan/relay/<id>/driver` is `"esp32-16x-relay"`          | `"esp32"`                                                                        |

## Why

- **Shared ESPHome packages, not copies per board.** The WiFi, OTA, MQTT, info and health pattern (ADR 0011: info published before `online`, a retained health with a last will) is the same on every ESP32 firmware. One repo holds it once as shared ESPHome packages that each board variant includes, instead of copying it into a repo per board and letting the copies drift.
- **One release and one CI.** A single tag, a single firmware CI gate (ADR 0015) and a single manifest check cover every variant, instead of one release process per board.
- **A discoverable home.** An owner looking for "the ESP32 firmware" finds one repo that lists every supported board, rather than a growing list of `driver-esp32-*` repos.
- **One key rule, no bookkeeping.** An earlier draft of this ADR made each variant its own Driver, keyed by the variant. That made `driver-esp32` the single repo whose key was not its name minus `driver-`, needed a per-variant manifest and a rule for choosing each variant's key, and left the key and the repo permanently able to disagree. Keying the repo as `esp32` keeps ADR 0014's rule with no exception, one manifest, and nothing to keep in sync.

## Costs accepted

- **Boards of different types share one Driver.** Every ESP32 board publishes the same retained `renewvan/esp32/info` and the same retained `renewvan/esp32/health` with one last will. The last `info` published wins, so the Settings page shows the version, Board and address of whichever board spoke last. One board dying marks the Driver `offline` even though the others are fine. Today the van has one ESP32 board, so none of this shows. **Adding a second ESP32 board type must revisit this key**: either a distinct Driver key per board type (a wire move of the two topics and every entity's `driver` value, with the retained-topic clearing that implies) or a per-board key derived by the firmware. That is deliberately deferred, not solved here.
- **Two identical boards are still unsupported.** They would publish the same `renewvan/esp32/info`, `renewvan/esp32/health` and the same entity ids, which ADR 0008 forbids.
- **One shared version across variants.** The tag is every variant's `version`, so a fix for one board bumps all of them, and a van running two boards shows the same version for both even if only one changed.
- **A variant name is no longer a Driver key.** The firmware file `esp32-16x-relay-driver.yaml`, the manifest file and the ESPHome node name keep the variant name, so a reader meets `esp32-16x-relay` in the repo and `esp32` on the bus. The glossary and `docs/authoring-a-driver.md` say so.

## Supersedes (naming only)

This supersedes the **naming rule of ADR 0012** (a Driver whose hardware is a purchasable board is named by its board, which fixed one repo per board) and the **repo name and Device key of ADR 0016** (`driver-esp32-16x-relay` and `esp32-16x-relay`, with the two retained topics under that key). ADRs 0012 and 0016 are historical records and are left as written. Their other decisions stand, including the retired-key rule of ADR 0012.

## Why this is an ADR

Hard to reverse: the repo name, the ESPHome project layout, the release tag and the Driver key `esp32` with its two retained topics are pinned by name from the hub's docs, from owners' flash scripts and from the dashboard, and the key is a public contract. Surprising without context: the repo, the firmware file and the Driver key now carry three different names (`driver-esp32`, `esp32-16x-relay`, `esp32`), while the accepted ADRs 0012 and 0016 still describe one repo per board keyed by the board. The result of a real trade-off: one repo per board keeps every Driver's version, release, health and info independent, at the cost of copying the shared ESPHome pattern into each; one repo and one Driver gives a single key rule and a shared pattern, at the cost of one shared info and health for every board.

## Cutover and what is not done here

- **The GitHub repo rename is a separate manual step** (`gh repo rename driver-esp32` on `renewvan/driver-esp32-16x-relay`). Until it is done, links and `git remote` URLs that name the new repo do not resolve; GitHub redirects the old name afterwards.
- **The relay firmware must be reflashed** to publish under `esp32`. Until then the van still publishes `renewvan/esp32-16x-relay/info|health` and `"esp32-16x-relay"` as each relay's `driver`, which no Driver claims. After reflashing, clear the stale retained topics once: `mosquitto_pub -r -n -t renewvan/esp32-16x-relay/info` and the same for `.../health`. The relay's `driver` topic is overwritten by the reflashed firmware.
- Docs here that name the repo (`README.md`, `CONTEXT.md`, `docs/authoring-a-driver.md`) say `driver-esp32`; the `firmware` release kind of ADR 0015 now names the same repo.

## See also

- `docs/adr/0012-board-named-relay-node.md`, `docs/adr/0016-esp32-16x-relay-rename.md`: the naming rule, repo name and key this supersedes.
- `docs/adr/0014-*.md`, `docs/adr/0018-device-to-driver-rename.md`: the key rule (repo name minus `driver-`) and the **Driver** vocabulary; the ESP32 firmware is a Driver that is also its own hardware.
- `docs/adr/0015-one-release-process-via-reusable-workflows.md`: the `firmware` release kind.
- `CONTEXT.md`: **Naming convention**, **Driver artifact naming**.
