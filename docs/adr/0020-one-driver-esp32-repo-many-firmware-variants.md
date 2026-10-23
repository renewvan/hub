---
status: accepted
---

# One `driver-esp32` repo, one Driver per firmware variant

All ESP32 firmware lives in one repo, `renewvan/driver-esp32` (was `driver-esp32-16x-relay`; before that `driver-esp32-relay`). Today it ships one firmware, for the 16-channel relay board. Later it is to hold the others (for example a Bluetooth hub, an OBD2 transceiver, a temperature and humidity sensor).

One physical board runs one firmware image, so **each variant is still its own Driver** with its own `manifest.json`, its own key and its own `renewvan/<key>/info` and `renewvan/<key>/health`. The relay's key stays `esp32-16x-relay`: there is no wire change, no topic moves, and no entity or Driver info payload changes.

| Was                                                                   | Now                                                                        |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Repo `driver-esp32-16x-relay`, image-less firmware repo (ADR 0015)    | `driver-esp32`, still a `firmware` release, one tag for every variant      |
| A Driver's key is its repo name minus `driver-` (ADR 0014, ADR 0012)  | True for every repo except this one, which ships several Drivers           |
| A Driver whose hardware is a purchasable board is named by that board | Each variant in `driver-esp32` is keyed by its variant (`esp32-16x-relay`) |
| Driver key `esp32-16x-relay`                                          | Unchanged                                                                  |

## Why

- **Shared ESPHome packages, not copies per board.** The WiFi, OTA, MQTT, info and health pattern (ADR 0011: info published before `online`, a retained health with a last will) is the same on every ESP32 firmware. One repo holds it once as shared ESPHome packages that each board variant includes, instead of copying it into a repo per board and letting the copies drift.
- **One release and one CI.** A single tag, a single firmware CI gate (ADR 0015) and a single manifest check cover every variant, instead of one release process per board.
- **A discoverable home.** An owner looking for "the ESP32 firmware" finds one repo that lists every supported board, rather than a growing list of `driver-esp32-*` repos.

## Costs accepted

- **One shared version across variants.** The tag is every variant's `version`, so a fix for one board bumps all of them, and a van running two boards shows the same version for both even if only one changed.
- **The key-per-board rule.** A key identifies one Driver, which is one firmware variant. Two identical boards would publish the same `renewvan/<key>/info` and `renewvan/<key>/health` and the same entity ids, which ADR 0008 forbids. That is not supported yet; it would need the repo to derive a distinct key (and distinct ids) per board, and it does not.
- **A repo is no longer exactly one Driver.** The rule "a Driver's key is its repo name minus `driver-`" holds for every other repo; here a Driver's key is its variant's name, and each variant carries its own manifest, validated against `schema/driver-manifest.schema.json` by the manifest check. The glossary and `docs/authoring-a-driver.md` say so.

## Supersedes (naming only)

This supersedes the **naming rule of ADR 0012** (a Driver whose hardware is a purchasable board is named by its board, which fixed one repo per board) and the **repo name of ADR 0016** (`driver-esp32-16x-relay`; the Driver key `esp32-16x-relay` that ADR 0016 chose is unchanged). ADRs 0012 and 0016 are historical records and are left as written. Their other decisions stand, including the retired-key rule of ADR 0012.

## Why this is an ADR

Hard to reverse: the repo name, the ESPHome project layout and the release tag are pinned by name from the hub's docs and from owners' flash scripts, and the Driver key stays a public contract. Surprising without context: the one repo whose name does not match its Driver key, and the first repo that ships several Drivers, while the accepted ADRs 0012 and 0016 still describe one repo per board. The result of a real trade-off: one repo per board keeps every Driver's version and release independent, at the cost of copying the shared ESPHome pattern into each.

## Cutover and what is not done here

- **The GitHub repo rename is a separate manual step** (`gh repo rename driver-esp32` on `renewvan/driver-esp32-16x-relay`). Until it is done, links and `git remote` URLs that name the new repo do not resolve; GitHub redirects the old name afterwards.
- Nothing on a running van changes: the Driver key `esp32-16x-relay`, its topics and its entity topics are the same, so no retained topic needs clearing and no redeploy is forced by this ADR.
- Docs here that name the repo (`README.md`, `CONTEXT.md`, `docs/authoring-a-driver.md`) say `driver-esp32`; the `firmware` release kind of ADR 0015 now names the same repo.

## See also

- `docs/adr/0012-board-named-relay-node.md`, `docs/adr/0016-esp32-16x-relay-rename.md`: the naming rule and repo name this supersedes.
- `docs/adr/0018-device-to-driver-rename.md`: the **Driver** vocabulary; the ESP32 firmware is a Driver that is also its own hardware.
- `docs/adr/0015-one-release-process-via-reusable-workflows.md`: the `firmware` release kind.
- `CONTEXT.md`: **Naming convention**, **Driver artifact naming**.
