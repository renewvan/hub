# plugins/kiosk

Display power bridge for the kiosk Pi: display sleep/wake, brightness, and
an idle-based auto-sleep timer.

## MQTT

| Topic                                                   | Direction          | Retained | Payload                                                            |
| ------------------------------------------------------- | ------------------ | -------- | ------------------------------------------------------------------ |
| `renewvan/kiosk/display/power/set`                      | dashboard → plugin | no       | `"on"` or `"off"`                                                  |
| `renewvan/kiosk/display/power`                          | plugin → consumers | **yes**  | `"on"` or `"off"`                                                  |
| `renewvan/kiosk/display/brightness/set`                 | dashboard → plugin | no       | `integer` 0–100 (normalized, rescaled to the panel's native range) |
| `renewvan/kiosk/display/brightness`                     | plugin → consumers | **yes**  | `integer` 0–100                                                    |
| `renewvan/kiosk/display/auto-sleep-enabled/set`         | dashboard → plugin | no       | `boolean`                                                          |
| `renewvan/kiosk/display/auto-sleep-enabled`             | plugin → consumers | **yes**  | `boolean` (default `false`)                                        |
| `renewvan/kiosk/display/auto-sleep-timeout-minutes/set` | dashboard → plugin | no       | `integer`, one of `1`/`5`/`15`/`30`                                |
| `renewvan/kiosk/display/auto-sleep-timeout-minutes`     | plugin → consumers | **yes**  | `integer` (default `5`)                                            |

Payload schemas: `schema/kiosk-display-{power,brightness,auto-sleep-enabled,auto-sleep-timeout-minutes}.schema.json`.

`power/set` applies unconditionally regardless of who sent it — there is no
remote/local distinction on sleep commands. There used to be a
remote-sleep-allowed gate restricting _remote_ `off` commands; it was
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
`_display_on_origin` in `kiosk.py`): a remote wake never triggers
auto-sleep on its own, so leaving the dashboard open remotely doesn't
fight the idle timer.

## Install (Pi)

Two scripts do everything, from the repo root on your machine:

```bash
bin/bootstrap-pi.sh          # once: Docker + /opt/renewvan layout
bin/deploy.sh                # rsyncs plugins/kiosk/, installs renewvan-kiosk
bin/bootstrap-kiosk-pi.sh    # once: cage + Chromium session on tty1, boot config, udev
sudo reboot                  # on the Pi
```

`bin/bootstrap-kiosk-pi.sh` targets Raspberry Pi OS **Lite** (Debian 13): no
desktop, no display manager. It installs `cage` and `chromium`, writes
`renewvan-cage.service`, disables any display manager, and applies the panel
rotation (`--no-rotate` skips it). Run `deploy.sh` first: the bootstrap reads the
unit from `/opt/renewvan/plugins/kiosk/`.

Two systemd units, two jobs:

| Unit             | Runs                                 | Purpose                                            |
| ---------------- | ------------------------------------ | -------------------------------------------------- |
| `renewvan-kiosk` | `kiosk.py` (Python venv)             | MQTT bridge: display power, brightness, auto-sleep |
| `renewvan-cage`  | `cage` → `renewvan-kiosk-browser.sh` | The on-screen session: fullscreen Chromium on tty1 |

`renewvan-cage` gets its seat from logind (`PAMName=login` on tty1), so there is
no `seatd`, display manager or autologin. cage exits when Chromium exits and
`Restart=always` brings the session back; `bin/deploy.sh` reloads the dashboard
by restarting this unit (and clearing Chromium's cache).

The Chromium command line lives only in `renewvan-kiosk-browser.sh`. It waits up
to 60 s for the dashboard, then `exec`s Chromium; `KIOSK_URL` overrides the URL.

### Rotation and touch

Rotation is done once, in the kernel: `video=DSI-1:800x480M@60D,panel_orientation=upside_down`
in `/boot/firmware/cmdline.txt`. cage has no output transform, so nothing in the
compositor can double-flip it. The ft5x06 touch controller ignores the KMS
orientation, so `/etc/udev/rules.d/99-ft5x06-rotation.rules` rotates its
coordinates with a libinput calibration matrix.

### No mouse cursor

cage draws a cursor only while its seat has a pointer capability. The Pi's HDMI ports
expose CEC remote-control input (`vc4-hdmi-0`, `vc4-hdmi-1`), which libinput classes as
`keyboard pointer`, so a kiosk with only a touchscreen still got a cursor.
`/etc/udev/rules.d/99-renewvan-ignore-hdmi-cec.rules` sets `LIBINPUT_IGNORE_DEVICE=1` on
them, leaving `touch` as the only capability, and cage hides the cursor. Nothing here uses
CEC. If you plug in a real mouse the cursor returns; `WLR_NO_HARDWARE_CURSORS=1` (set in
the unit) keeps it inside the rotated framebuffer when that happens.

## Configuration

All config via environment variables — set them in the systemd unit's `[Service]` block or in `/etc/renewvan/kiosk.env`:

| Variable            | Default                | Description                                                                                 |
| ------------------- | ---------------------- | ------------------------------------------------------------------------------------------- |
| `MQTT_HOST`         | `localhost`            | MQTT broker hostname                                                                        |
| `MQTT_PORT`         | `1883`                 | MQTT broker port                                                                            |
| `MQTT_USERNAME`     | _(empty)_              | Broker username                                                                             |
| `MQTT_PASSWORD`     | _(empty)_              | Broker password                                                                             |
| `DISPLAY_ON_CMD`    | _(empty = `bl_power`)_ | Command to power the display on. Empty: write `0` to the backlight's `bl_power`             |
| `DISPLAY_OFF_CMD`   | _(empty = `bl_power`)_ | Command to power the display off. Empty: write `1` to `bl_power`                            |
| `DISPLAY_QUERY_CMD` | _(empty = `bl_power`)_ | Command to read state. Empty: read `bl_power`. Output parsed as `0`/`1`, wlopm, or vcgencmd |
| `BRIGHTNESS_DEVICE` | _(auto-detect)_        | `/sys/class/backlight/<id>` directory; auto-detects the first backlight device if unset     |

### Display power

The default drives `/sys/class/backlight/<id>/bl_power` directly. That works under
cage, which has no output-power protocol, so `wlopm` does not work there.
`bin/bootstrap-kiosk-pi.sh` installs a udev rule (`99-renewvan-backlight.rules`)
making `bl_power` group-writable by `video`, which the unit's user is in. The rule
only fires when a device is added and the panel is already registered, so the script
also runs `udevadm trigger` to apply it (and the touch rotation rule) to the live
devices; without that, sleep fails with `Permission denied` until the next reboot.

To use an external command instead (a panel without a backlight node), set all
three variables:

```ini
# /etc/renewvan/kiosk.env
DISPLAY_ON_CMD=vcgencmd display_power 1
DISPLAY_OFF_CMD=vcgencmd display_power 0
DISPLAY_QUERY_CMD=vcgencmd display_power
```

## Logs

```bash
journalctl -u renewvan-kiosk -f
```
