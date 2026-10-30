# plugins/hub

Publishes the Pi's own local-network address and the hub's version to the renewvan MQTT bus, so the dashboard can show the hub's IP and, under Settings › General › Firmware, which hub release is running. It reads the address from the Pi itself, so it works whether or not a router Driver is installed. It also owns the shared **Primary battery** setting (below). Shipped by default; nothing to configure.

## MQTT topics

| Topic                              | Retained | Direction       |
| ---------------------------------- | -------- | --------------- |
| `renewvan/hub/status`              | yes      | plugin → bus    |
| `renewvan/hub/primary-battery`     | yes      | plugin → bus    |
| `renewvan/hub/primary-battery/set` | no       | client → plugin |
| `renewvan/hub/icons`               | yes      | plugin → bus    |
| `renewvan/hub/icons/set`           | no       | client → plugin |

### `renewvan/hub/status`

**Payload** (JSON, `schema/hub-status.schema.json`):

```json
{
  "ip": "192.168.10.50",
  "hostname": "renewvan",
  "version": "v0.4.0"
}
```

| Field      | Type           | Description                                                                                                                                          |
| ---------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ip`       | string \| null | Source IPv4 of the Pi's default route. `null` when the Pi has no default route.                                                                      |
| `hostname` | string \| null | The Pi's hostname. `null` when unreadable.                                                                                                           |
| `version`  | string         | The hub release deployed on the Pi: `git describe --tags --always --dirty` of the checkout `bin/deploy.sh` ran from, or `dev` when none was stamped. |

`ip` is the address the Pi uses to reach the network, found by asking the kernel which local address it would send from (a UDP socket is connected to a reserved test address; nothing is transmitted). That skips the Docker bridges and the Tailscale address, which are not where a phone or laptop on the van's network reaches the Pi. The Tailscale address is published separately on `renewvan/tailscale/status`.

`version` is never read from a file you edit. `bin/deploy.sh` writes it to `/opt/renewvan/plugins/hub/VERSION` after syncing the plugin, because the Pi has no `.git`. A tagged clean checkout reads `v0.4.0`; commits past a tag read `v0.4.0-3-g6a77a25`; an untagged tree reads a bare commit hash; uncommitted changes append `-dirty`. A Pi deployed before this existed, or a plugin run from a checkout, publishes `dev`. Same convention as a Driver's `version` (`docs/authoring-a-driver.md`).

Published every 30 seconds, so a broker restart that lost retained state heals within one poll.

### `renewvan/hub/primary-battery`

One battery bank can be published by two Drivers (for example driver-venus and driver-wattcycle), so which _instance_ is the headline battery is a setting, and it is shared: every client (dashboard on the kiosk, a phone, a laptop) reads the same value from this topic.

**Payload** (JSON string, `schema/hub-primary-battery.schema.json`): `"auto"` or a battery instance id such as `"512"` or `"wattcycle"`.

```json
"auto"
```

A client changes it by publishing the same payload, unretained, to `renewvan/hub/primary-battery/set` (the same command-then-state convention as the kiosk plugin's `…/brightness/set`). The plugin persists the value and republishes it retained on `renewvan/hub/primary-battery`; clients never write the state topic themselves.

- **Default `"auto"`.** Nothing is persisted until the first valid command.
- **Persisted on the Pi** in `/var/lib/renewvan-hub/primary-battery.json` (override with `PRIMARY_BATTERY_PATH`), so the choice survives a plugin, broker or Pi restart. The unit declares `StateDirectory=renewvan-hub`, so systemd creates the directory owned by the service user. The file is replaced atomically. The kiosk plugin keeps its settings only as retained broker state, so it is not a precedent for the file itself.
- **Published on every (re)connect**: the persisted value, retained, so a broker that lost retained state heals as soon as the plugin reconnects.
- **Not validated against instances.** The plugin does not know which batteries exist. It rejects only a payload that is not a non-empty JSON string (non-JSON, a number, an empty string): the payload is ignored and logged, the state is left as it was. An id that is not on the bus (or not complete) is the consumer's problem: the dashboard falls back to `auto` and says so.
- If the file cannot be written, the command is logged as an error and not applied, rather than publishing a value that would silently revert on restart.

### `renewvan/hub/icons`

Which icon every client draws an entity instance with. An icon is a presentation choice, so it is a shared setting here and never a property of the entity (`docs/adr/0023-entity-icons-are-a-shared-hub-setting.md`): the kiosk, a phone and a laptop read the same map, and no Driver has to know a dashboard's icon vocabulary.

**Payload** (JSON object, `schema/hub-icons.schema.json`): each key is `<entity>/<id>`, the entity domain and instance id of `renewvan/<entity>/<id>/...`; each value is a lucide component name (PascalCase, letters and digits only).

```json
{ "temperature/outdoor": "Park", "tank/fresh": "Droplet" }
```

A client changes one entry by publishing a command, unretained, to `renewvan/hub/icons/set` (`schema/hub-icon-set.schema.json`); `icon: null` clears the entry:

```json
{ "key": "tank/fresh", "icon": "Waves" }
```

The plugin persists the whole map and republishes it retained on `renewvan/hub/icons`; clients never write the state topic themselves.

- **Default `{}`.** Nothing is persisted until the first valid command.
- **Persisted on the Pi** in `/var/lib/renewvan-hub/icons.json` (override with `ICONS_PATH`), replaced atomically, so the icons survive a plugin, broker or Pi restart.
- **Published on every (re)connect**, retained, so a broker that lost retained state heals as soon as the plugin reconnects.
- **Shape only.** The plugin does not know which instances exist or which icons a consumer can draw. It rejects a command that is not exactly `{key, icon}` with a `<entity>/<id>` key and a PascalCase name (or `null`): the command is ignored and logged. A stored entry of the wrong shape is dropped on its own, never costing the others. A consumer ignores an entry whose instance is not on the bus and falls back to its own icon for a name it cannot draw.
- If the file cannot be written, the command is logged as an error and not applied, rather than publishing a map that would silently revert on restart.
- The last command wins when two clients set the same key at once.

## Setup

`bin/deploy.sh` installs and restarts the plugin on every deploy. No authentication step.

## Configuration

Override defaults in `/etc/renewvan/hub.env` (seeded on first deploy):

```sh
# MQTT_HOST=localhost
# MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=
# POLL_INTERVAL=30
# PRIMARY_BATTERY_PATH=/var/lib/renewvan-hub/primary-battery.json
# ICONS_PATH=/var/lib/renewvan-hub/icons.json
```

Restart the service after changes: `sudo systemctl restart renewvan-hub`

## Verify

```sh
mosquitto_sub -h <pi> -t renewvan/hub/status -C 1
mosquitto_sub -h <pi> -t renewvan/hub/primary-battery -C 1
mosquitto_sub -h <pi> -t renewvan/hub/icons -C 1
mosquitto_pub -h <pi> -t renewvan/hub/icons/set -m '{"key":"tank/fresh","icon":"Droplet"}'
mosquitto_pub -h <pi> -t renewvan/hub/primary-battery/set -m '"auto"'
journalctl -u renewvan-hub -n 20
```

## Tests

```sh
cd plugins/hub && python3 -m unittest test_hub_status -v   # or: pytest plugins/hub
```
