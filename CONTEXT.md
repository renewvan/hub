# renewvan Hub

An open-hardware, vendor-agnostic campervan control platform: a unified device model for van hardware (tanks, relays, batteries), published on a single MQTT bus by nodes, consumed by a read-only dashboard and logging. See `review/renewvan-hub-research-report.md` for the full research/architecture rationale, and `.scratch/renewvan-hub-v0/map.md` for the v0 spec effort.

## Language

**Entity**:
A category of van hardware in the unified device model (`tank`, `relay`, `battery`), each with a fixed field list published on the MQTT bus. The machine-checkable contract for each entity's fields is the JSON Schema under [`/schema`](schema/) (`tank.schema.json`, `relay.schema.json`, `battery.schema.json`).
_Avoid_: Device, thing, component (too generic — use the specific entity name).

**Renewvan bus**:
The single MQTT broker every node publishes to and every consumer (dashboard, logger) reads from, under the `renewvan/<domain>/<id>/<property>` topic convention.
_Avoid_: Message bus, broker (when the campervan-specific bus is meant, not MQTT generically); "Van bus" (superseded — the topic root is `renewvan`, not `van`).

**Renewvan hub**:
The Docker Compose stack running on the Pi that forms the connective core of the platform: the Mosquitto broker (renewvan bus), pinned node containers, and plugins. From a user's perspective, "Renewvan hub connected" means the dashboard has an active MQTT-over-WebSocket connection to this stack. From an operator's perspective, "the hub" refers to the Pi-side compose deployment managed by this repo.
_Avoid_: "Bus connected" (ambiguous — sounds like a vehicle CAN/RV-C bus or the MQTT protocol itself, not the Pi service); "hub service" (the hub is the whole stack, not one service inside it).

**Tank**:
A fluid reservoir (fresh water, grey water, black water, fuel, or LPG) monitored by a resistive level sensor. Reports `fluid_type`, `capacity_l`, `level_pct` (raw, fill percentage not volume), `level_pct_smoothed` (rate-extrapolated display value for stepped senders), `status`, `fill_rate_lpm`/`drain_rate_lpm`, and `volume_since_full_l`/`volume_since_empty_l`, plus optionally `alarm_state`, `temperature_c`, and the `last_inspected_at`/`last_full_at`/`last_empty_at` timestamps.
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

**Kiosk plugin** (`plugins/kiosk`):
A Pi-host process (systemd service `renewvan-node-kiosk`, not a Docker container) in `plugins/kiosk/` that bridges the renewvan bus to OS-level backlight control via the `bl_power` sysfs interface. Subscribes to `renewvan/kiosk/display/power/set`; publishes current state retained to `renewvan/kiosk/display/power`. Also monitors the touch device via evdev and wakes the display on any touch event while the backlight is off.
_Avoid_: "kiosk daemon", "display service", "node-kiosk" (deprecated directory name).

**Tailscale plugin** (`plugins/tailscale`):
A Pi-host process (systemd service `renewvan-tailscale`) in `plugins/tailscale/` that connects the Pi to a Tailscale tailnet and publishes VPN status retained to `renewvan/tailscale/status`. Payload fields: `enabled` (daemon running), `connected` (authenticated + online), `ip` (Tailscale IPv4), `hostname`, `peers` (online peer count). Ships installed by `bin/deploy.sh`; requires one-time `sudo tailscale up` to authenticate.
_Avoid_: "VPN node", "Tailscale node" (plugins are not MQTT nodes — they run on the Pi host but have a different contract).

**Display power state** (`renewvan/kiosk/display/power`):
A retained MQTT topic carrying `"on"` or `"off"` (see `schema/kiosk-display-power.schema.json`). Published by the kiosk node after each successful `vcgencmd display_power` call, and initialised from the live hardware state on node startup. The dashboard subscribes to this topic to drive its sleeping overlay. The matching command topic (`renewvan/kiosk/display/power/set`) carries the same payload but is not retained.

**Node artifact naming** (blanket rule, not a per-repo call): a node's repo, Docker image, and `hub`-side pin all carry the same role qualifier. Pattern: repo `renewvan/node-<entity>`, image `ghcr.io/renewvan/node-<entity>`. The role prefix comes first in every artifact name. `logger` and `dashboard` are exempt (bare `renewvan/logger`, `renewvan/dashboard`). The `hub`-side pin is the `image:` tag in `docker-compose.yml` itself (git-tracked, no env var indirection — see `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`), not an `.env` value.
