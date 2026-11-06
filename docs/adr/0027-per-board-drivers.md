---
status: accepted
---

# One Driver per ESP32 board, keyed `esp32-<name>`

A van can run any number of ESP32 relay boards, including several of the same kind. Each **board** is its own **Driver**, keyed `esp32-<name>`, where `<name>` is chosen by the owner once, when the board is flashed. The repo `driver-esp32` stays one repo that ships many firmware **variants**, but a variant is only a kind of firmware: it is built once, runs on any number of boards, and never appears as a key, a topic or an id on the bus.

This supersedes the "one board per van" limit that ADR 0020 accepted (one Driver `esp32`, one shared `info`, `health` and last will for every board), restates the key rule of ADR 0014 and the "Ids" bullet of ADR 0013, and removes the `driver` fallback of ADR 0019 for relays. The decisions come from the `driver-esp32` wayfinder map (`.wayfinder/map.md`, tickets 01 to 07). Nothing is deployed, so no data is migrated.

| Was                                                                               | Now                                                                                                    |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| One Driver `esp32` for the whole repo, whatever the boards (ADR 0020)             | One Driver per board, `esp32-<name>`, each with its own `info`, `health` and last will                 |
| Retained `renewvan/esp32/info` and `renewvan/esp32/health`                        | `renewvan/esp32-<name>/info` and `/health`; no `esp32` family Driver and no `renewvan/esp32/*`         |
| A Driver's key is its repo name minus `driver-` (ADR 0014)                        | True for a repo that is one Driver. A repo may be many Drivers; `driver-esp32` keys are `esp32-<name>` |
| Relay ids are `relay_01` to `relay_16`, no schema constrains them (ADR 0013)      | Relay ids are opaque slugs, `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`; `driver-esp32` builds them `<board>-<nn>` |
| Relay `driver` optional; absent means every Driver whose info `publishes` `relay` | `driver` is required on every relay; there is no fallback                                              |
| Driver info has no notion of the firmware kind                                    | Optional, informational `variant`; consumers must not branch on it                                     |

## Rules this sets

- **The key rule.** A Driver's key is the `<driver>` segment of its `info` and `health` topics and the value of `driver` on every entity it publishes. For a repo that is one Driver it is the repo name minus `driver-` (ADR 0014, unchanged). `driver-esp32` is the one repo that holds many Drivers: each board is `esp32-<name>`. The name is set at flash time and never changes; it is a slug that does not end in `-<digits>`, so an id is never ambiguous with a board name. The shared Driver slug pattern (no `driver-` prefix, `demo` reserved) is unchanged.
- **Variants stay off the bus.** A variant selects firmware, names the firmware file and the ESPHome project, and may be reported as `info.variant`. It is never a key, never part of an id, and no consumer reads it to decide anything.
- **Ids are opaque slugs.** `relay.schema.json` carries the grammar `^[a-z][a-z0-9]*(-[a-z0-9]+)*$` as `definitions/id`. The id is a topic segment and not a payload field, so a payload schema cannot check it: `schema/validate-examples.sh` validates the fixtures under `schema/examples/ids/` against it and `schema/validate-demo.py` validates every relay demo id. The schema does not require an id to start with its Driver's key, so a bridge Driver may name channels differently. The `<board>-<nn>` shape (zero-padded, positional per board, for example `front-01`) is a `driver-esp32` convention, documented there only. A consumer that parses an id for a board or channel has a bug.
- **`driver` is required on a relay.** It is the only link from an id to its Driver. Nothing predates it, so the "absent means every Driver that publishes relay" fallback is removed from the relay contract. Other entities keep ADR 0019's optional `driver`.
- **One source per id** (ADR 0013) is kept. Two boards cannot share a name, so they cannot share an id; prevention is by construction and no detection is built. A retained `driver` that flips on one id signals a misconfiguration.
- **Health stays per Driver.** There is no per-relay availability topic. A bridge that must report one dead device separately runs one Driver per device, each with its own key and last will.
- **ADR 0013's wire contract is not relaxed for bridges.** `set` is JSON `true` or `false` only (no toggle), `state` is a retained JSON boolean published after the output changed, every channel is off after a restart, and `name` is retained. A bridge owns the translation: it republishes state, never forwards a toggle, forces its devices off at startup, and stores `name`.

## Contract neutrality

The shared contract must stay open to a relay that is not an ESP32 (a Sonoff on ESPHome, a Tasmota or Shelly device behind a bridge Driver). So:

- No relay property and no `info` field may require anything ESP32-specific: a firmware variant, ESPHome, a firmware version, or a board kind.
- Consumers must not branch on `info.variant`, on the shape of an id, or on the prefix of a Driver key.
- Variant, manifest and board-name detail stay in `driver-esp32`. The Driver manifest has no `variant` field and the schema set gains nothing ESP32-specific.
- Adding a relay family is a new Driver (in `driver-esp32` as a variant when it is ESPHome firmware, in its own repo when it is a bridge), never a change to `relay.schema.json`.

## Why

- **Per-board health and info.** With one shared `esp32` Driver, the last `info` published won and one board dying marked every board `offline` (the cost ADR 0020 accepted and told the next board to revisit). A Driver per board gives each its own version, address, serial and last will.
- **Opaque ids need a grammar, not a prefix rule.** A slug is enough for a topic segment and for the logger, which keys history by id alone. Tying the id to the Driver key would make the shared schema ESP32-shaped.
- **`driver` required.** The fallback joined an instance to every Driver that publishes the entity, which is wrong the moment two Drivers publish `relay`. With nothing deployed there is no Driver that predates the field.
- **A variant is not an identity.** Keying by variant forced every board of a kind to share a key. Keying by name lets a van carry two identical boards.

## Rejected alternatives

- **Global ids (`relay_01` to `relay_24`) or manifest-assigned ids.** Both need a central list. Rejected.
- **A van-level board manifest.** Boards self-describe through their retained `info`; labels and expected-board lists are dashboard or hub configuration, not wire contract.
- **An id pattern that requires the Driver key as a prefix.** Rejected: it would force a bridge Driver to name channels like an ESP32.
- **A per-relay availability topic.** Rejected: a last will carries one topic, and a Driver per device already gives per-device health.
- **Keeping the `esp32` family Driver beside per-board Drivers.** Rejected: it would be a Driver that publishes nothing and a retained `renewvan/esp32/*` nobody owns.

## Why this is an ADR

Hard to reverse: Driver keys, relay ids and the required `driver` are public wire contract that the boards, the dashboard, the logger and the broker ACLs depend on. Surprising without context: the repo `driver-esp32` is many Drivers while every other repo is one, and ADR 0020 still says one Driver `esp32`. The result of a real trade-off: one shared Driver is simpler, at the cost of no per-board health and no second board.

## Not done here

- **Firmware and flashing in `driver-esp32`**: the `--board <name>` build and flash flow, per-board secrets, and flash-time validation of the board name.
- **Broker ACL.** `bin/broker-accounts.sh` grants the `esp32` account `renewvan/esp32/#`; per-board keys need that widened (for example `renewvan/esp32-+/#`). Separate change.
- **Clearing retained topics** of a renamed or removed board (a ghost Driver) is a manual step, as in ADR 0020's cutover.
- **Dashboard** (not built here): it needs a per-Driver replacement for the single `relayDriversOnline` boolean, which assumes one relay Driver, and should treat a retained `driver` that flips on one id as a misconfiguration signal. Grouping channels by board and naming boards is dashboard configuration.
- Docs elsewhere that describe the single `esp32` Driver (`README.md` of `driver-esp32`, the Settings page) change with that repo.

## Supersedes

- The one-board-per-van limit, and the "Costs accepted" of **ADR 0020**: its Driver key `esp32` and the retained `renewvan/esp32/*` topics. Its single repo, many variants, one release and one CI stand.
- The **"Ids"** bullet of **ADR 0013** (`relay_01` to `relay_16`, "no schema constrains an id"). The rest of ADR 0013 stands. Its "Node" wording is read with ADR 0014 and ADR 0018, and its "Unknown" rule is now per Driver per board.
- The **key rule** of **ADR 0014** ("a key is its repo name minus `driver-`") as a universal rule, per above.
- The optional `driver` and its fallback of **ADR 0019**, for `relay` only.

ADRs 0013, 0014, 0019 and 0020 are historical records and are left as written, as in ADR 0018.

## See also

- `docs/adr/0013-writable-relay-and-its-wire-contract.md`, `docs/adr/0014-node-to-device-rename.md`, `docs/adr/0019-entity-instances-name-their-driver-and-shared-primary-battery.md`, `docs/adr/0020-one-driver-esp32-repo-many-firmware-variants.md`: the decisions this restates.
- `docs/adr/0008-node-naming-and-entity-neutrality.md`: required properties only what every source can supply.
- `docs/adr/0010-demo-mode-publishes-under-renewvan-demo.md`: the demo ids overlap real ids, so demo relay ids are `demo-01` to `demo-05` and the simulated board is `esp32-demo`.
- `schema/relay.schema.json`, `schema/driver-info.schema.json`.
