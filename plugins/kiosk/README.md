# renewvan/node-kiosk

Display power bridge for the kiosk Pi: display sleep/wake, brightness, a
remote-sleep-allowed gate, and an idle-based auto-sleep timer.

## MQTT

| Topic | Direction | Retained | Payload |
|---|---|---|---|
| `renewvan/kiosk/display/power/set` | dashboard → node | no | `"on"` or `"off"` (`off` rejected when the gate below is disabled, unless the command arrives within `LOCAL_COMMAND_GRACE_S` of a physical touch — see below) |
| `renewvan/kiosk/display/power` | node → consumers | **yes** | `"on"` or `"off"` |
| `renewvan/kiosk/display/remote-sleep-allowed/set` | dashboard → node | no | `boolean` |
| `renewvan/kiosk/display/remote-sleep-allowed` | node → consumers | **yes** | `boolean` (default `true`) |
| `renewvan/kiosk/display/brightness/set` | dashboard → node | no | `integer` 0–100 (normalized, rescaled to the panel's native range) |
| `renewvan/kiosk/display/brightness` | node → consumers | **yes** | `integer` 0–100 |
| `renewvan/kiosk/display/auto-sleep-enabled/set` | dashboard → node | no | `boolean` |
| `renewvan/kiosk/display/auto-sleep-enabled` | node → consumers | **yes** | `boolean` (default `false`) |
| `renewvan/kiosk/display/auto-sleep-timeout-minutes/set` | dashboard → node | no | `integer`, one of `1`/`5`/`15`/`30` |
| `renewvan/kiosk/display/auto-sleep-timeout-minutes` | node → consumers | **yes** | `integer` (default `5`) |

Payload schemas: `schema/kiosk-display-{power,remote-sleep-allowed,brightness,auto-sleep-enabled,auto-sleep-timeout-minutes}.schema.json`.

The remote-sleep-allowed gate restricts `off` commands arriving over MQTT —
but only ones with no local provenance. `power/set` has no origin field
(the same dashboard code runs whether loaded in the van's own kiosk
Chromium or on a remote phone), so a physical touch on the host screen
within `LOCAL_COMMAND_GRACE_S` (3s) of the command is treated as proof it
came from someone physically at the van, and bypasses the gate — the gate
exists to stop a remote device sleeping the display out from under someone
standing at it, never to stop the host sleeping itself. Waking (`on`) is
never gated, and the auto-sleep timer (below) sleeps the display itself
independent of this gate — it's a host-local decision, not a remote
command.

Auto-sleep reuses the same evdev touch device as touch-to-wake as its idle
clock: any touch (not just a waking one) resets it. When enabled, the
display sleeps itself after the configured idle minutes with no touch.

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

# 4. Install the boot-time Chromium kiosk launcher. System-wide, not the
#    per-user labwc autostart file — labwc runs both, and only the system
#    file suppresses the default desktop's panel/file-manager icons.
#    bin/deploy.sh relaunches Chromium after every deploy with the same
#    URL; this file only matters for a cold boot/reflash.
sudo cp plugins/kiosk/labwc-autostart /etc/xdg/labwc/autostart
sudo chmod +x /etc/xdg/labwc/autostart
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
| `BRIGHTNESS_DEVICE` | _(auto-detect)_ | `/sys/class/backlight/<id>` directory; auto-detects the first backlight device if unset |

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
