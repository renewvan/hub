# plugins/hub

Publishes the Pi's own local-network address and the hub's version to the renewvan MQTT bus, so the dashboard can show the hub's IP and, under Settings › General › Firmware, which hub release is running. It reads the address from the Pi itself, so it works whether or not a router Device is installed. Shipped by default; nothing to configure.

## MQTT topic

| Topic                 | Retained | Direction    |
| --------------------- | -------- | ------------ |
| `renewvan/hub/status` | yes      | plugin → bus |

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

`version` is never read from a file you edit. `bin/deploy.sh` writes it to `/opt/renewvan/plugins/hub/VERSION` after syncing the plugin, because the Pi has no `.git`. A tagged clean checkout reads `v0.4.0`; commits past a tag read `v0.4.0-3-g6a77a25`; an untagged tree reads a bare commit hash; uncommitted changes append `-dirty`. A Pi deployed before this existed, or a plugin run from a checkout, publishes `dev`. Same convention as a Device's `version` (`docs/authoring-a-device.md`).

Published every 30 seconds, so a broker restart that lost retained state heals within one poll.

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
```

Restart the service after changes: `sudo systemctl restart renewvan-hub`

## Verify

```sh
mosquitto_sub -h <pi> -t renewvan/hub/status -C 1
journalctl -u renewvan-hub -n 20
```

## Tests

```sh
cd plugins/hub && python3 -m unittest test_hub_status -v
```
