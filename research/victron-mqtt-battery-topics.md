# Research: Victron Venus OS MQTT topic scheme for battery/BMS data

**Date**: 2026-09-24
**Purpose**: Resolve wayfinder ticket 05 (`.scratch/campervan-hub-v0/issues/05-victron-mqtt-battery-topics-research.md`) — feeds ticket 04's mapping of Victron's native battery data onto the v0.1 `battery` entity's `van/battery/<id>/<property>` topics.

## 1. Topic path structure

Venus OS bridges internal D-Bus properties to MQTT via `dbus-mqtt` (https://github.com/victronenergy/dbus-mqtt). Every readable topic follows:

```
N/{portalId}/{serviceType}/{deviceInstance}/{DbusPath}
```

- `portalId` — the VRM Portal ID of the GX device (e.g. `e0ff50a097c0`).
- `serviceType` — D-Bus service class, e.g. `system`, `battery`, `vebus`, `charger`, `settings`.
- `deviceInstance` — instance number when more than one device of that service type exists; `system` service is always instance `0`.
- `DbusPath` — the D-Bus object path for the property, e.g. `/Dc/Battery/Voltage`.

Writable settings/commands use `W/...` (write) and `R/...` (read-request) prefixes on the same path shape; `N/...` is the read/notify side. dbus-mqtt publishes on D-Bus `PropertiesChanged` (change-driven, not polled).

## 2. Battery-relevant topics (primary source: victronenergy/venus-html5-app `TOPICS.md`)

**Aggregate battery list** (covers multiple BMS instances, e.g. multiple lithium packs):

```
Topic:   N/{portalId}/system/0/Batteries
Payload: {"value": [
  {
    "soc": 100.0,
    "active_battery_service": true,
    "temperature": 30.0,
    "power": 472.85,
    "current": 8.30,
    "instance": 256,
    "state": 1,
    "voltage": 56.98,
    "id": "com.victronenergy.battery.ttyO0",
    "name": "BMV-702"
  }
]}
```

**Individual system-level DC battery topics** (single "main" battery, simpler for single-battery-bank vans):

```
N/{portalId}/system/0/Dc/Battery/Voltage      -> {"value": 210}   (example units: V*10 in some firmware — verify actual scale against your unit's live payload)
N/{portalId}/system/0/Dc/Battery/Current
N/{portalId}/system/0/Dc/Vebus/Current        <- charge/discharge current between battery and inverter/charger
N/{portalId}/system/0/Dc/Vebus/Power
N/{portalId}/system/0/SystemState/State       <- charge state enum (bulk/absorption/float/storage/etc.)
```

**Direct per-BMS-service topics** (if querying the battery's own D-Bus service rather than the `system` aggregate), pattern:

```
N/{portalId}/battery/{instance}/Soc
N/{portalId}/battery/{instance}/Dc/0/Voltage
N/{portalId}/battery/{instance}/Dc/0/Current
N/{portalId}/battery/{instance}/Dc/0/Power
```//(instance and exact leaf paths enumerate at runtime — see §3)

Payload envelope for scalar values is uniform: `{"value": <number>}`.

## 3. Discovering available topics on a running system

- Subscribe to `N/{portalId}/#` (or `N/+/#` if portalId unknown) on the Venus device's local broker; Venus OS republishes every current value shortly after a fresh subscribe (a "full publish" burst), so a wildcard subscribe is the fastest way to enumerate everything actually present on a specific installation instead of guessing paths from docs.
- Cross-reference discovered paths against the D-Bus API spec (https://github.com/victronenergy/venus/wiki/dbus) for the authoritative property list per service type.
- ha-victron-mqtt (https://github.com/tomer-w/ha-victron-mqtt) auto-generates entities (400+) from these topics and publishes its own topic→entity mapping doc at https://tomer-w.github.io/victron_mqtt/ — useful as a cross-reference for which battery fields are considered "stable"/commonly available across installations, without re-deriving from raw D-Bus paths.

## 4. Connection / auth requirements

- **MQTT must be enabled on the GX device** first: *Settings → Integrations → MQTT Access* (older Venus OS), or automatic via MQTT pairing mode on Venus OS v3.80+ (*Settings → Integrations → MQTT Devices → Pairing mode*, active 120s, or double-press the GX device's physical button on screenless units).
- **Local Network Security Profile** on the GX device gates the exact connection method:
  - *Unsecured* + MQTT already on: plain connect, no credentials, port 1883.
  - *Unsecured* first-time / *Weak* / *Secured*: SSL on port 8883, via MQTT pairing mode (v3.80+) or username `remoteconsole` + the GX device's password (older versions / non-unsecured profiles).
- Rooted/SSH-enabled Venus OS installs can alternatively connect as user `root` with the SSH password, SSL on 8883.
- **Keepalive quirk**: Venus OS's own MQTT client only keeps publishing to a topic while at least one external subscriber is connected to the broker for the `N/#` tree (documented via the "full publish" behavior on subscribe, and multiple community reports of topics going stale with no active subscriber) — a v0 driver bridging this data should keep a persistent subscription rather than connect/read/disconnect.
- For lower load or to avoid connecting Home-Assistant-side consumers directly to the Venus broker, a Mosquitto bridge config is a documented pattern (ha-victron-mqtt README, Method 3): bridge `topic N/# in 0` (and `W/#`, `R/#` outbound) from the Venus device's broker into a local Mosquitto instance, then have consumers (including this project's future `van/*` bridge) read from the local broker instead of connecting to the GX device directly.

## Sources

- victronenergy/venus-html5-app, `TOPICS.md` (official, primary): https://github.com/victronenergy/venus-html5-app/blob/master/TOPICS.md
- victronenergy/dbus-mqtt (the D-Bus↔MQTT bridge service itself): https://github.com/victronenergy/dbus-mqtt
- victronenergy/venus D-Bus API wiki: https://github.com/victronenergy/venus/wiki/dbus
- tomer-w/ha-victron-mqtt README (connection/security-profile matrix, Mosquitto bridge pattern): https://github.com/tomer-w/ha-victron-mqtt
- tomer-w's auto-generated entity/topic reference: https://tomer-w.github.io/victron_mqtt/

## Open item for ticket 04

Confirm on your actual GX device/hardware which shape is live for your setup: the `system/0/Batteries` aggregate array (handles multiple battery services cleanly) vs. individual `system/0/Dc/Battery/*` scalars (simpler, single-battery assumption). Recommend mapping ticket 04 onto the `Batteries` aggregate topic since it's forward-compatible with adding a second battery bank later, and already carries `soc`, `voltage`, `current`, `temperature` in one payload.
