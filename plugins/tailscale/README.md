# plugins/tailscale

Connects the Pi to your [Tailscale](https://tailscale.com) tailnet and publishes VPN status to the renewvan MQTT bus. Shipped by default; only activates after you run `sudo tailscale up`.

## What it does

- Installs the `tailscale` package and enables `tailscaled` on the Pi.
- Polls `tailscale status` every 30 seconds and publishes the result to MQTT.
- Gives remote access to SSH, the dashboard, and any other port over Tailscale — no port forwarding or public IP needed.

## MQTT topic

| Topic                       | Retained | Direction    |
| --------------------------- | -------- | ------------ |
| `renewvan/tailscale/status` | yes      | plugin → bus |

**Payload** (JSON):

```json
{
  "enabled": true,
  "connected": true,
  "ip": "100.64.0.1",
  "hostname": "renewvan",
  "peers": 2
}
```

| Field       | Type           | Description                 |
| ----------- | -------------- | --------------------------- |
| `enabled`   | bool           | `tailscaled` is running     |
| `connected` | bool           | Authenticated and online    |
| `ip`        | string \| null | Tailscale IPv4 address      |
| `hostname`  | string \| null | Machine name in the tailnet |
| `peers`     | integer        | Online peer count           |

When not connected, `ip` and `hostname` are `null` and `peers` is `0`.

## Setup

`bin/deploy.sh` installs the plugin automatically. After deploying, authenticate once on the Pi:

```sh
ssh renewvan
sudo tailscale up
```

Follow the URL printed to authorise the device in your tailnet. Once authorised the MQTT bridge starts publishing connected state within 30 seconds.

## Configuration

Override defaults in `/etc/renewvan/tailscale.env` (seeded on first deploy):

```sh
# MQTT_HOST=localhost
# MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=
# POLL_INTERVAL=30
```

Restart the service after changes: `sudo systemctl restart renewvan-tailscale`

## Remote access

Once connected, the Pi is reachable at its Tailscale address or MagicDNS name. Only Caddy's port 443 (dashboard, `/mqtt`, `/influx`) and SSH are open on it: the dashboard container's port 80 and the broker's ports are not published on the tailnet.

| Service   | URL                                                                                                                                                |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dashboard | `https://<magicdns-name>` (`renewvan.<tailnet>.ts.net`), then the shared Caddy login. `https://<tailscale-ip>` works but warns on the certificate. |
| SSH       | `ssh <pi-user>@<tailscale-ip>`                                                                                                                     |
| Grafana   | `ssh -L 3000:localhost:3000 <pi-user>@<tailscale-ip>` (loopback-only on the Pi)                                                                    |
| MQTT      | Through the dashboard's `/mqtt` proxy, or `ssh -L 1883:localhost:1883 <pi-user>@<tailscale-ip>` (the broker is not on the tailnet)                 |

`bin/deploy.sh` seeds `TAILSCALE_BIND_ADDR` and `TAILSCALE_DNS_NAME` from `tailscale` into `/opt/renewvan/hub/.env`. See [HTTPS on the LAN](../../docs/reference.md#https-on-the-lan) for the certificate and the login.
