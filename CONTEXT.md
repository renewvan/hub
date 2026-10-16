# renewvan Hub

An open-hardware, vendor-agnostic campervan control platform: a unified device model for van hardware (tanks, relays, batteries, solar controllers, chargers, routers), published on a single MQTT bus by nodes, consumed by a read-only dashboard and logging. See `review/renewvan-hub-research-report.md` for the full research/architecture rationale, and `.scratch/renewvan-hub-v0/map.md` for the v0 spec effort.

## Language

**Entity**:
A category of van hardware in the unified device model (`tank`, `relay`, `battery`, `solar`, `charger`, `router`, `gps`, `temperature`, `tilt`), each with a field list published on the MQTT bus. The machine-checkable contract for each entity's fields is the JSON Schema under [`/schema`](schema/) (`tank.schema.json`, `relay.schema.json`, `battery.schema.json`, `solar.schema.json`, `charger.schema.json`, `router.schema.json`, `gps.schema.json`, `temperature.schema.json`, `tilt.schema.json`).
An entity is named for what the hardware _is_, never for a brand or protocol (`battery`, not `victron-battery`): the schema is where vendor-neutrality lives. Each schema requires only the properties every source can supply and marks the rest optional, so consumers must tolerate an optional property being absent. See `docs/adr/0008-node-naming-and-entity-neutrality.md`.
Every entity must also have a demo scenario in `schema/demo/`, so it shows up in **Demo mode**: see `docs/demo-mode.md`.
_Avoid_: Device, thing, component (too generic — use the specific entity name).

**Renewvan bus**:
The single MQTT broker every node publishes to and every consumer (dashboard, logger) reads from, under the `renewvan/<domain>/<id>/<property>` topic convention.
_Avoid_: Message bus, broker (when the campervan-specific bus is meant, not MQTT generically); "Van bus" (superseded — the topic root is `renewvan`, not `van`).

**Renewvan hub**:
The Docker Compose stack running on the Pi that forms the connective core of the platform: the Mosquitto broker (renewvan bus), pinned node containers, and plugins. From a user's perspective, "Renewvan hub connected" means the dashboard has an active MQTT-over-WebSocket connection to this stack. From an operator's perspective, "the hub" refers to the Pi-side compose deployment managed by this repo.
_Avoid_: "Bus connected" (ambiguous — sounds like a vehicle CAN/RV-C bus or the MQTT protocol itself, not the Pi service); "hub service" (the hub is the whole stack, not one service inside it).

**Tank**:
A fluid reservoir (fresh water, grey water, black water, fuel, or LPG) monitored by a resistive level sensor. Reports `fluid_type`, `capacity_l`, `level_pct` (raw, fill percentage not volume), `status`, and `volume_since_full_l`/`volume_since_empty_l`, plus optionally `alarm_state`/`alarm_direction`/`alarm_threshold_pct`/`alarm_restore_pct`, `temperature_c`, and the `last_inspected_at`/`last_full_at`/`last_empty_at` timestamps.
_Avoid_: Reservoir.

**Relay**:
A binary on/off load switched by the relay node (ESPHome firmware on an ESP32 host, e.g. a light circuit). Reports only `state` in v0 — no command/control topic yet.
_Avoid_: Switch, load (relay is the wire-schema entity name).

**Battery**:
A battery bank as seen by a shunt, BMS, or inverter-charger, whatever the vendor. Always reports `soc_pct`, `voltage_v`, `current_a`, `power_w`; optionally `temperature_c` (only with a probe), `time_to_go_s` (present whenever the source reports a usable estimate, regardless of charge state; absent when it reports none), `time_since_full_charge_s`, and `charge_state` (only from a source that knows the charge stage — a plain shunt does not). Today sourced from Venus OS via `renewvan/node-venus` (`system/0/Batteries`, id = Venus device instance); other sources publish the same entity from their own node.
_Avoid_: Shunt, BMS (the underlying hardware, not this project's entity name). A BMS's per-cell data is not `battery`; it gets its own `bms` entity when a BMS node exists.

**Solar**:
A solar charge controller (MPPT/PWM), whatever the vendor, one entity per controller. Always reports `power_w` and `battery_voltage_v`; optionally `battery_current_a`, `pv_voltage_v`, `pv_current_a`, `yield_today_wh`, `yield_history_wh` (the controller's own daily yield log as one retained array, newest first, index 0 = today, `null` = a day with no data; see `docs/adr/0009-solar-yield-history-as-one-retained-array.md`), and the controller's own `charge_state`. Sourced from Venus OS `solarcharger/<instance>/*` via `renewvan/node-venus`.
_Avoid_: MPPT, PV (hardware/jargon, not the entity name); `charger` (that entity is for non-solar chargers).

**Charger**:
A non-solar battery charger, whatever the vendor: an AC mains charger (e.g. a Victron BlueSmart) or a DC-DC charger fed by the alternator (e.g. an Orion). Only `voltage_v` is required; optionally `current_a`, `power_w`, `input_voltage_v`, `source` (`ac`/`alternator`/`dc`) and `charge_state`. Sourced from Venus OS `charger/<instance>` and `alternator/<instance>` via `renewvan/node-venus`.
_Avoid_: Booster, orion (a product line, and "booster" names no vendor-neutral thing); alternator charger (that is a `source` value, not the entity).

**Router**:
The van's uplink router — the single device connecting the van to the internet — reporting connectivity status: `signal_rsrp_dbm`/`signal_rsrq_db`/`signal_sinr_db`/`signal_rssi_dbm`, `operator`, `network_type`, `uptime_s`, and monthly SIM data usage (`data_used_month_tx_b`/`data_used_month_rx_b`). Sourced from the router vendor's push feed via the `renewvan/node-router` node, like the Venus node remaps an existing feed.
_Avoid_: Gateway, modem (hardware inside the router, not this entity), RUT (vendor name), WAN (networking jargon — the entity's field is `network_type`).

**GPS**:
Live position telemetry (no identity/startup-only fields) from a USB GPS/GLONASS/BeiDou module read directly by the `renewvan/node-gps` node (NMEA 0183 over serial — sensing, not a feed remap). Reports `fix_quality`, `satellites_in_use`, `avg_snr_db` unconditionally (`avg_snr_db` is independent of fix status — mean per-satellite signal strength from GSV sentences, present even before a fix); `latitude`, `longitude`, `altitude_m`, `hdop`, `speed_kmh`, `course_deg`, `fix_time` only once `fix_quality != no_fix`.
_Avoid_: Location, position (too generic — use the entity name); GNSS (the schema/topic name is `gps` even though the module also tracks GLONASS/BeiDou).

**Temperature**:
A standalone temperature sensor (1-Wire DS18B20 probe or the Pi's own SoC thermal zone) read directly by the `renewvan/node-temperature` node (sensing, not a feed remap), keyed by a user-assigned sensor id. Reports `name`, `unit`, `source` (and `serial` for 1-Wire) as retained identity fields, and `temperature_c`/`status` live. `temperature_c` is always Celsius on the wire; `unit` (`C`/`F`) is a display preference the dashboard applies. `name` and `unit` are user-editable via `<field>/set` command topics and persisted node-side, overriding the `config.ini` default. Temperatures that belong to another entity's hardware (tank water temperature, battery temperature) stay on that entity's own `temperature_c` and are not republished here.
_Avoid_: Thermometer, probe (the hardware, not the entity); sensor (too generic, see Entity); publishing the same reading under two entities.

**Tilt**:
Van inclination from a GY-521 / MPU-6050 accelerometer read directly by the `renewvan/node-tilt` node (sensing, not a feed remap). Live fields only, no identity fields: `roll_deg` (positive = left side down), `pitch_deg` (positive = nose up), both in the van frame (x forward, y left, z up), smoothed and corrected by an install-time **level zero** captured via `renewvan/tilt/<id>/level_zero/set` (`reset` clears it) and persisted node-side; `status` (`ok`/`sensor_error`). Whether the van counts as "level" is a tolerance the dashboard applies — the node publishes angles, not a level flag.
_Avoid_: Level, inclination, slope (the entity/topic name is `tilt`); an `is_level` boolean on the wire (a presentation threshold would force a node release to retune).

**Node**:
Anything that publishes one specific device or feed onto the renewvan bus, normalized into the device model — whether it's firmware on an ESP32 (the relay node) or a process on the Pi (the tank node), and whether it owns the sensing (the tank node's ADS1115 math) or remaps an _existing_ external feed (the Venus node, remapping Venus OS's MQTT feed onto `renewvan/battery|solar|charger/<id>/...`). A node is named for its _source_ (`node-venus`, `node-wattcycle`), not for the entity it publishes: one node may publish several entities, and several nodes may publish the same entity.
_Avoid_: Driver (superseded — one term for firmware and process alike), Bridge (a node that remaps an existing feed is still a Node).

**Host**:
The physical hardware a node runs on (the Raspberry Pi, an ESP32 board, a wall panel). One host may run several nodes; a node is never a host.
_Avoid_: Node (for the hardware), board, device.

**Demo mode**:
A simulated van on the renewvan bus, so the dashboard (and any other consumer) can be built and shown without a van or hardware. Modelled on Venus OS's Demo mode. One switch shared by every client: any client publishes `renewvan-demo/enabled/set`, **node-demo** answers with the retained `renewvan-demo/enabled` and starts or clears the simulated van, and every dashboard follows that flag (off by default). Real nodes keep publishing on `renewvan/`, ignored while it is on. Read-only: command topics are not simulated. See `docs/demo-mode.md` and `docs/adr/0010-demo-mode-on-a-separate-topic-root.md`.
_Avoid_: Simulation mode, test mode, mock mode.

**Demo root** (`renewvan-demo/`):
The topic root `node-demo` publishes under, parallel to `renewvan/`: `renewvan-demo/<entity>/<id>/<property>`, with the same property names, JSON encoding and retained flag, plus `renewvan-demo/health` (`online`/`offline`, LWT). Never published on `renewvan/`, because real retained state would be overwritten and the logger (which subscribes `renewvan/#`) would write fake data to InfluxDB.
_Avoid_: Demo bus, demo broker (it is the same broker, a different root), `renewvan/demo/...` (it must not match `renewvan/#`).

**Demo scenario** (`schema/demo/<entity>[.<name>].demo.json`):
A hub-owned file describing one simulated entity instance: an `initial` record plus a `timeline` of `set` overlays over a `loop_s` loop, validated by `schema/validate-demo.py` against the entity's schema. Scenarios are per entity, not per source, and share one clock so entities stay coherent. Venus analogue: a `dbus-recorder` recording.
_Avoid_: Fixture (those are `schema/examples/`, static valid/invalid payloads), recording (nothing is captured live), mock.

**node-demo**:
The process that replays every demo scenario onto the **Demo root**. A Node in mechanism (a Pi-side process with an LWT health topic) that always runs but is idle until `renewvan-demo/enabled` is `true`, so a real van shows nothing fake unless someone switches it on but a simulator in purpose: it owns no hardware and remaps no feed, so it is named for what it is, not for a source.
_Avoid_: Mock node, fake driver, simulator node.

**Naming convention**:
Repos are named plainly, scoped by the `renewvan` GitHub org — not a stuttering `renewvan-<name>`; the org already provides the namespace, so re-prefixing every repo restates it. Exception: Node repos carry a `node-` role prefix (`renewvan/node-tank`, `renewvan/node-venus`, `renewvan/node-relay`), because bare `tank`/`relay` collides with the **Entity** name the moment repo, image, and env var are referenced side by side. `logger`, `dashboard`, and `mobile` aren't Nodes, so they stay bare.

**Kiosk plugin** (`plugins/kiosk`):
A Pi-host process (systemd service `renewvan-node-kiosk`, not a Docker container) in `plugins/kiosk/` that bridges the renewvan bus to OS-level backlight control via the `bl_power` sysfs interface. Subscribes to `renewvan/kiosk/display/power/set`; publishes current state retained to `renewvan/kiosk/display/power`. `power/set` applies unconditionally — no remote/local gating, see `docs/kiosk-display-sleep-wake.md`'s Design decisions for why a prior remote-sleep-allowed gate was removed. Also monitors the touch device via evdev and wakes the display on any touch event while the backlight is off, and drives an opt-in auto-sleep idle timer off the same touch stream, scoped to host-local sessions only (`_display_on_origin`). Also owns brightness (`renewvan/kiosk/display/brightness`[`/set`], the sibling backlight sysfs file to `bl_power`, normalized 0-100 on the wire). See `plugins/kiosk/README.md` for the full topic table.
_Avoid_: "kiosk daemon", "display service", "node-kiosk" (deprecated directory name).

**Tailscale plugin** (`plugins/tailscale`):
A Pi-host process (systemd service `renewvan-tailscale`) in `plugins/tailscale/` that connects the Pi to a Tailscale tailnet and publishes VPN status retained to `renewvan/tailscale/status`. Payload fields: `enabled` (daemon running), `connected` (authenticated + online), `ip` (Tailscale IPv4), `hostname`, `peers` (online peer count). Ships installed by `bin/deploy.sh`; requires one-time `sudo tailscale up` to authenticate.
_Avoid_: "VPN node", "Tailscale node" (plugins are not MQTT nodes — they run on the Pi host but have a different contract).

**Display power state** (`renewvan/kiosk/display/power`):
A retained MQTT topic carrying `"on"` or `"off"` (see `schema/kiosk-display-power.schema.json`). Published by the kiosk plugin after each successful `vcgencmd display_power` call, and initialised from the live hardware state on startup. The dashboard reflects it via `DisplaySleepButton`'s icon only — no on-screen "sleeping" overlay (removed, see `docs/kiosk-display-sleep-wake.md`'s Design decisions: redundant with the physical backlight cut and with `node_kiosk.py`'s own touch grab, and it needed increasingly complex machinery to avoid also rendering on remote viewers). The only visible effect of `"off"` is the Pi's own backlight cutting. The matching command topic (`renewvan/kiosk/display/power/set`) carries the same payload but is not retained.

**Node artifact naming** (blanket rule, not a per-repo call): a node's repo, Docker image, and `hub`-side pin all carry the same role qualifier. Pattern: repo `renewvan/node-<source>`, image `ghcr.io/renewvan/node-<source>`, where `<source>` is the entity for a node that owns the sensing of one kind of hardware (`node-tank`, `node-tilt`) and the protocol or brand for a node that bridges a source (`node-venus`); see `docs/adr/0008-node-naming-and-entity-neutrality.md`. The role prefix comes first in every artifact name. `logger` and `dashboard` are exempt (bare `renewvan/logger`, `renewvan/dashboard`). The `hub`-side pin is the `image:` tag in `docker-compose.yml` itself (git-tracked, no env var indirection — see `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`), not an `.env` value.
