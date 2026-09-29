# renewvan/node-kiosk

Display power bridge for the kiosk Pi. Subscribes to the renewvan bus command topic and calls `vcgencmd display_power` (or a configured substitute) to physically cut or restore the DSI panel's backlight.

## MQTT

| Topic | Direction | Retained | Payload |
|---|---|---|---|
| `renewvan/kiosk/display/power/set` | dashboard → node | no | `"on"` or `"off"` |
| `renewvan/kiosk/display/power` | node → consumers | **yes** | `"on"` or `"off"` |

Payload schema: `schema/kiosk-display-power.schema.json`.

## Install (Pi)

```bash
# 1. Copy to /opt/renewvan/plugins/kiosk/
sudo mkdir -p /opt/renewvan/node-kiosk
sudo cp plugins/kiosk/node_kiosk.py plugins/kiosk/requirements.txt /opt/renewvan/plugins/kiosk/

# 2. Install Python deps (system-level, no venv needed on Pi OS)
pip3 install -r /opt/renewvan/plugins/kiosk/requirements.txt

# 3. Install + enable systemd service
sudo cp plugins/kiosk/renewvan-node-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now renewvan-node-kiosk.service
```

## Configuration

All config via environment variables — set them in the systemd unit's `[Service]` block or in `/etc/renewvan/node-kiosk.env`:

| Variable | Default | Description |
|---|---|---|
| `MQTT_HOST` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | _(empty)_ | Broker username |
| `MQTT_PASSWORD` | _(empty)_ | Broker password |
| `DISPLAY_ON_CMD` | `vcgencmd display_power 1` | Command to power display on |
| `DISPLAY_OFF_CMD` | `vcgencmd display_power 0` | Command to power display off |
| `DISPLAY_QUERY_CMD` | `vcgencmd display_power` | Command to read current state (stdout parsed for `display_power=0/1`) |

### Wayland fallback

If `vcgencmd display_power` does not suit the attached panel, switch to the `wlopm` compositor-level fallback with a config-only change:

```ini
# /etc/renewvan/node-kiosk.env
DISPLAY_ON_CMD=wlopm --on '*'
DISPLAY_OFF_CMD=wlopm --off '*'
DISPLAY_QUERY_CMD=wlopm
```

`wlopm` with no arguments lists outputs and their power state; the node logs the raw output and marks state unknown (no retained publish) — supply a wrapper script if state initialisation matters for your setup.

## Logs

```bash
journalctl -u renewvan-node-kiosk -f
```
