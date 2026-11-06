# plugins/hub

Publishes the Pi's own local-network address and the hub's version to the renewvan MQTT bus, so the dashboard can show the hub's IP and, under Settings › General › Firmware, which hub release is running. It reads the address from the Pi itself, so it works whether or not a router Driver is installed. It also owns the shared **Primary battery**, entity **icons** and **relay groups** settings (below). Shipped by default; nothing to configure.

## MQTT topics

| Topic                               | Retained | Direction       |
| ----------------------------------- | -------- | --------------- |
| `renewvan/hub/status`               | yes      | plugin → bus    |
| `renewvan/hub/primary-battery`      | yes      | plugin → bus    |
| `renewvan/hub/primary-battery/set`  | no       | client → plugin |
| `renewvan/hub/icons`                | yes      | plugin → bus    |
| `renewvan/hub/icons/set`            | no       | client → plugin |
| `renewvan/hub/relay-groups`         | yes      | plugin → bus    |
| `renewvan/hub/relay-groups/set`     | no       | client → plugin |
| `renewvan/hub/relay-group-list`     | yes      | plugin → bus    |
| `renewvan/hub/relay-group-list/set` | no       | client → plugin |
| `renewvan/hub/relay-order`          | yes      | plugin → bus    |
| `renewvan/hub/relay-order/set`      | no       | client → plugin |

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
{ "temperature/outdoor": "Park", "tank/fresh": "Droplet", "relay/relay-x8-01": "Lightbulb" }
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

### `renewvan/hub/relay-groups`

Which group (Lights, Appliances, USB, ...) every client files a relay channel under. A grouping is a presentation choice, so it is a shared setting here and never a property of the relay (`docs/adr/0028-relay-groups-are-a-shared-hub-setting.md`): the kiosk, a phone and a laptop read the same map, and the relay Driver never learns what a channel switches.

**Payload** (JSON object, `schema/hub-relay-groups.schema.json`): each key is a relay id, the `<id>` of `renewvan/relay/<id>/...`; each value is a group name of 1 to 32 characters with no leading or trailing whitespace.

```json
{ "relay-x8-01": "Lights", "relay-x8-04": "USB" }
```

A client changes one entry by publishing a command, unretained, to `renewvan/hub/relay-groups/set` (`schema/hub-relay-group-set.schema.json`); `group: null` clears the entry, so the relay is Unassigned again:

```json
{ "id": "relay-x8-01", "group": "Lights" }
```

The plugin persists the whole map and republishes it retained on `renewvan/hub/relay-groups`; clients never write the state topic themselves.

- **Default `{}`.** Nothing is persisted until the first valid command; every relay is Unassigned.
- **Persisted on the Pi** in `/var/lib/renewvan-hub/relay-groups.json` (override with `RELAY_GROUPS_PATH`), replaced atomically, so the groups survive a plugin, broker or Pi restart.
- **Published on every (re)connect**, retained, so a broker that lost retained state heals as soon as the plugin reconnects.
- **Shape only.** The plugin does not know which relays exist or which group names a consumer offers. It rejects a command that is not exactly `{id, group}` with a relay id and a group name (or `null`): the command is ignored and logged. A stored entry of the wrong shape is dropped on its own, never costing the others. A consumer ignores an entry whose relay is not on the bus.
- If the file cannot be written, the command is logged as an error and not applied, rather than publishing a map that would silently revert on restart.
- The last command wins when two clients set the same relay at once.

### `renewvan/hub/relay-group-list`

The groups a client offers for a relay and the order it lists them in. Shared, so the owner edits it once for every client.

**Payload** (JSON array, `schema/hub-relay-group-list.schema.json`): up to 16 distinct group names of 1 to 32 characters (no leading or trailing whitespace); `[]` is valid.

```json
["Lights", "Unassigned", "Appliances", "USB"]
```

A client replaces the whole list by publishing the same payload, unretained, to `renewvan/hub/relay-group-list/set`; the plugin persists it and republishes it retained.

- **Default** the five stock groups (`Lights`, `Appliances`, `USB`, `Water`, `Heating`), until the first valid command (publishing the default writes nothing to disk).
- **Unassigned** stands for the relays with no group. Where its name sits is where a client draws them (last when it is absent).
- **Persisted on the Pi** in `/var/lib/renewvan-hub/relay-group-list.json` (override with `RELAY_GROUP_LIST_PATH`), replaced atomically; **published on every (re)connect**, retained.
- **The assignments are not touched** when a name leaves the list: a consumer reads an assignment to a name that is not in the list as Unassigned, and the client that renames or removes a group moves or clears its relays first.
- A command that is not a JSON array of valid, distinct names (or has more than 16) is ignored and logged; an unwritable file means the command is not applied.

### `renewvan/hub/relay-order`

The order a client lists relay channels in inside a group card: one array of relay ids, first to last. Shared, so the kiosk and a phone agree.

**Payload** (JSON array, `schema/hub-relay-order.schema.json`): up to 512 distinct relay ids; default `[]`.

```json
["relay-x8-02", "relay-x8-01", "relay-x8-04"]
```

A client replaces the whole array by publishing the same payload, unretained, to `renewvan/hub/relay-order/set`; the plugin persists it and republishes it retained.

- **Persisted on the Pi** in `/var/lib/renewvan-hub/relay-order.json` (override with `RELAY_ORDER_PATH`), replaced atomically; **published on every (re)connect**, retained.
- **Shape only.** A relay missing from the array sorts after the listed ones; an id that is not on the bus is ignored by a consumer. A command that is not a JSON array of valid, distinct ids (or has more than 512) is ignored and logged; an unwritable file means the command is not applied.
- One array for every card: a card orders its own relays by their position in it, so moving a relay inside one card never disturbs another.

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
# RELAY_GROUPS_PATH=/var/lib/renewvan-hub/relay-groups.json
# RELAY_GROUP_LIST_PATH=/var/lib/renewvan-hub/relay-group-list.json
# RELAY_ORDER_PATH=/var/lib/renewvan-hub/relay-order.json
```

Restart the service after changes: `sudo systemctl restart renewvan-hub`

## Verify

```sh
mosquitto_sub -h <pi> -t renewvan/hub/status -C 1
mosquitto_sub -h <pi> -t renewvan/hub/primary-battery -C 1
mosquitto_sub -h <pi> -t renewvan/hub/icons -C 1
mosquitto_sub -h <pi> -t renewvan/hub/relay-groups -C 1
mosquitto_sub -h <pi> -t renewvan/hub/relay-group-list -C 1
mosquitto_pub -h <pi> -t renewvan/hub/relay-groups/set -m '{"id":"relay-x8-01","group":"Lights"}'
mosquitto_pub -h <pi> -t renewvan/hub/icons/set -m '{"key":"tank/fresh","icon":"Droplet"}'
mosquitto_pub -h <pi> -t renewvan/hub/primary-battery/set -m '"auto"'
journalctl -u renewvan-hub -n 20
```

## Tests

```sh
cd plugins/hub && python3 -m unittest test_hub_status -v   # or: pytest plugins/hub
```
