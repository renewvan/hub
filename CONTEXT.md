# RenewVan Hub

An open-hardware, vendor-agnostic campervan control platform: a unified device model for van hardware (tanks, relays, batteries), published on a single MQTT bus, consumed by drivers, a read-only dashboard, and logging. See `review/renewvan-hub-research-report.md` for the full research/architecture rationale, and `.scratch/renewvan-hub-v0/map.md` for the v0 spec effort.

## Language

**Entity**:
A category of van hardware in the unified device model (`tank`, `relay`, `battery`), each with a fixed field list published on the MQTT bus. The machine-checkable contract for each entity's fields is the JSON Schema under [`/schema`](schema/) (`tank.schema.json`, `relay.schema.json`, `battery.schema.json`).
_Avoid_: Device, thing, component (too generic — use the specific entity name).

**Van bus**:
The single MQTT broker every driver publishes to and every consumer (dashboard, logger) reads from, under the `van/<domain>/<id>/<property>` topic convention.
_Avoid_: Message bus, broker (when the campervan-specific bus is meant, not MQTT generically).

**Tank**:
A fluid reservoir (fresh water, grey water, black water, fuel, or LPG) monitored by a resistive level sensor. Reports `fluid_type`, `capacity_l`, `level_pct` (fill percentage, not volume), and `status`.
_Avoid_: Reservoir.

**Relay**:
A binary on/off load switched by the ESP32 relay node (e.g. a light circuit). Reports only `state` in v0 — no command/control topic yet.
_Avoid_: Switch, load (relay is the wire-schema entity name).

**Battery**:
The house battery bank, sourced from Victron's native Venus OS MQTT feed (`N/{portalId}/system/0/Batteries`), not a driver built by this project. Reports `soc_pct`, `voltage_v`, `current_a`, `power_w`, `temperature_c`, `charge_state`.
_Avoid_: Shunt, BMS (those are the underlying hardware/driver on the Victron side, not this project's entity name).

**Driver**:
A process that normalizes one hardware source into the van bus's device model (e.g. dbus-ads1115 for tanks, the ESP32 relay node for relays). Not every entity has a driver in v0 — battery data is bridged directly from Victron's existing feed.

**Bridge**:
A process that remaps an *existing* external feed (one this project doesn't own the sensing for) onto the van bus's device model — e.g. `battery`, which remaps Victron's own Venus OS MQTT feed onto `van/battery/<id>/...`. Contrast with `Driver`, which normalizes a sensor this project owns.
_Avoid_: Using "driver" for battery's remap component, or vice versa — the distinction (owned sensing vs. remapped external feed) is load-bearing for where new component repos live.

**Naming convention**:
Repos are named plainly, scoped by the `renewvan` GitHub org (`renewvan/tank`, `renewvan/battery`, `renewvan/logger`) — not a stuttering `renewvan-<name>`; the org already provides the namespace, so re-prefixing every repo restates it. A repo may still publish under a prefixed distribution name (PyPI/npm package, Docker tag) if a bare name risks colliding on an unnamespaced registry — a per-repo call, not a blanket rule.
