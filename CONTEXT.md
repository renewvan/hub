# renewvan Hub

An open-hardware, vendor-agnostic campervan control platform: a unified device model for van hardware (tanks, relays, batteries), published on a single MQTT bus by nodes, consumed by a read-only dashboard and logging. See `review/renewvan-hub-research-report.md` for the full research/architecture rationale, and `.scratch/renewvan-hub-v0/map.md` for the v0 spec effort.

## Language

**Entity**:
A category of van hardware in the unified device model (`tank`, `relay`, `battery`), each with a fixed field list published on the MQTT bus. The machine-checkable contract for each entity's fields is the JSON Schema under [`/schema`](schema/) (`tank.schema.json`, `relay.schema.json`, `battery.schema.json`).
_Avoid_: Device, thing, component (too generic — use the specific entity name).

**Renewvan bus**:
The single MQTT broker every node publishes to and every consumer (dashboard, logger) reads from, under the `renewvan/<domain>/<id>/<property>` topic convention.
_Avoid_: Message bus, broker (when the campervan-specific bus is meant, not MQTT generically); "Van bus" (superseded — the topic root is `renewvan`, not `van`).

**Tank**:
A fluid reservoir (fresh water, grey water, black water, fuel, or LPG) monitored by a resistive level sensor. Reports `fluid_type`, `capacity_l`, `level_pct` (fill percentage, not volume), and `status`.
_Avoid_: Reservoir.

**Relay**:
A binary on/off load switched by the relay node (ESPHome firmware on an ESP32 host, e.g. a light circuit). Reports only `state` in v0 — no command/control topic yet.
_Avoid_: Switch, load (relay is the wire-schema entity name).

**Battery**:
The house battery bank, sourced from Victron's native Venus OS MQTT feed (`N/{portalId}/system/0/Batteries`) via the `renewvan/node-battery` node. Reports `soc_pct`, `voltage_v`, `current_a`, `power_w`, `temperature_c`, `charge_state`.
_Avoid_: Shunt, BMS (those are the underlying hardware on the Victron side, not this project's entity name).

**Node**:
Anything that publishes one specific device or feed onto the renewvan bus, normalized into the device model — whether it's firmware on an ESP32 (the relay node) or a process on the Pi (the tank node), and whether it owns the sensing (the tank node's ADS1115 math) or remaps an *existing* external feed (the battery node, remapping Victron's Venus OS MQTT feed onto `renewvan/battery/<id>/...`).
_Avoid_: Driver (superseded — one term for firmware and process alike), Bridge (a node that remaps an existing feed is still a Node).

**Host**:
The physical hardware a node runs on (the Raspberry Pi, an ESP32 board, a wall panel). One host may run several nodes; a node is never a host.
_Avoid_: Node (for the hardware), board, device.

**Naming convention**:
Repos are named plainly, scoped by the `renewvan` GitHub org — not a stuttering `renewvan-<name>`; the org already provides the namespace, so re-prefixing every repo restates it. Exception: Node repos carry a `node-` role prefix (`renewvan/node-tank`, `renewvan/node-battery`, `renewvan/node-relay`), because bare `tank`/`battery`/`relay` collides with the **Entity** name the moment repo, image, and env var are referenced side by side. `logger`, `dashboard`, and `mobile` aren't Nodes, so they stay bare.

**Node artifact naming** (blanket rule, not a per-repo call): a node's repo, Docker image, and `hub`-side pin all carry the same role qualifier. Pattern: repo `renewvan/node-<entity>`, image `ghcr.io/renewvan/node-<entity>`, env var `NODE_<ENTITY>_IMAGE_TAG`. The role prefix comes first in every artifact name, so all node vars sort together and non-node vars (`LOGGER_IMAGE_TAG`, `DASHBOARD_IMAGE_TAG`) stand out clearly. `logger` and `dashboard` are exempt (`LOGGER_IMAGE_TAG`).
