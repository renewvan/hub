# renewvan/node-kiosk

Display power bridge for the kiosk Pi: display sleep/wake, brightness, and
an idle-based auto-sleep timer.

## MQTT

| Topic | Direction | Retained | Payload |
|---|---|---|---|
| `renewvan/kiosk/display/power/set` | dashboard → node | no | `"on"` or `"off"` |
| `renewvan/kiosk/display/power` | node → consumers | **yes** | `"on"` or `"off"` |
| `renewvan/kiosk/display/brightness/set` | dashboard → node | no | `integer` 0–100 (normalized, rescaled to the panel's native range) |
| `renewvan/kiosk/display/brightness` | node → consumers | **yes** | `integer` 0–100 |
| `renewvan/kiosk/display/auto-sleep-enabled/set` | dashboard → node | no | `boolean` |
| `renewvan/kiosk/display/auto-sleep-enabled` | node → consumers | **yes** | `boolean` (default `false`) |
| `renewvan/kiosk/display/auto-sleep-timeout-minutes/set` | dashboard → node | no | `integer`, one of `1`/`5`/`15`/`30` |
| `renewvan/kiosk/display/auto-sleep-timeout-minutes` | node → consumers | **yes** | `integer` (default `5`) |

Payload schemas: `schema/kiosk-display-{power,brightness,auto-sleep-enabled,auto-sleep-timeout-minutes}.schema.json`.

`power/set` applies unconditionally regardless of who sent it — there is no
remote/local distinction on sleep commands. There used to be a
remote-sleep-allowed gate restricting *remote* `off` commands; it was
removed (see `docs/kiosk-display-sleep-wake.md`'s Design decisions) once
sleeping the display stopped having any visible effect on any dashboard
viewer at all — local or remote (the dashboard's old sleeping overlay is
also gone, for the same reason: redundant with the backlight physically
cutting). A gate that only ever inconvenienced the legitimate dashboard
UI, while anyone who wanted to bypass it could just flip the same toggle
back on from Settings on the same remote device, wasn't protecting
anything.

Auto-sleep reuses the same evdev touch device as touch-to-wake as its idle
clock: any touch (not just a waking one) resets it. When enabled, the
display sleeps itself after the configured idle minutes with no touch —
but only for a session a physical touch originated (see
`_display_on_origin` in `node_kiosk.py`): a remote wake never triggers
auto-sleep on its own, so leaving the dashboard open remotely doesn't
fight the idle timer.

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
