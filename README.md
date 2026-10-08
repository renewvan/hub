# renewvan/hub

Open-source, open-hardware campervan control hub for Raspberry Pi, built on MQTT.

## What is this?

There's no shortage of ways to monitor a campervan's battery, tanks, and

appliances — but every option forces a trade-off. Victron's Venus OS is

mature but centered on Victron's own hardware. Home Assistant is fully

open but has no van-domain packaging out of the box: no tank calibration

workflow, no shunt-based state-of-charge accounting, no gas-safety

interlocks. Existing DIY platforms tend to drift toward their own

commercial hardware over time.

deseavan/hub takes a different approach: an MQTT broker as the single

source of truth, a unified device model for van-specific entities

(batteries, tanks, heaters, pumps, safety sensors), and first-class

bridges _into_ the ecosystems that already exist — Venus OS, Home

Assistant, Signal K, and RV-C — rather than competing with them. Any

driver, whether it's talking to a Victron MPPT over [VE.Direct](http://VE.Direct) or an

ESP32 relay node over ESPHome, normalizes into the same schema and

appears on the same bus.

The goal isn't to replace any of these platforms — it's to be the open,

vendor-agnostic glue between them.

## Status

Early / hobby-scale (v0). This repo (`hub`) hosts the shared device-model
schema, the MQTT/InfluxDB/Grafana deployment (docker-compose), and
project docs. Drivers, bridges, the logger, and the dashboard each live
in their own repo under the `renewvan` GitHub org (`node-tank`, `node-battery`,
`logger`) — see `.scratch/renewvan-hub-v0-build/` for the v0 build spec.
Not yet ready for production van use — see \[Roadmap\](#roadmap).

## Install

1. **Copy `.env.example` to `.env`** and fill in the required values
   (Victron GX device reachability, the dashboard's browser-reachable
   MQTT-WS URL) — see `.env.example`'s comments for what each value is
   and where it's used.
2. **Pull and start everything:** `docker compose pull && docker
compose up -d`. This brings up the infra layer (Mosquitto, InfluxDB,
   Grafana) alongside `node-tank`, `node-battery`, `logger`, and `dashboard` —
   each a pinned, independently-published image (own repo, own
   release; nothing built from source in `hub`, per
   `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`).
   Bumping a service later is a single `image:` tag edit in `docker-compose.yml`.
   `node-gps` is behind the `gps` compose profile (its `/dev/ttyACM0`
   passthrough would otherwise make Docker refuse to start and abort the
   whole `up -d` on a host with no GPS module): add `--profile gps` when the
   module is plugged in, or let `bin/deploy.sh` decide from the device.
3. **Flash the ESP32 relay node**, once, per `renewvan/node-relay`'s own
   README (ESPHome YAML, no custom firmware).
4. **Install the phone app** per `renewvan/mobile`'s own README (React
   Native — sideload or app-store build, not part of this compose flow).

`docker-compose.yml` is the plain-Compose-host path (named volumes). A
host-path/bind-mount deployment (e.g. TrueNAS's Custom App "Install via
YAML") is a personal deployment concern, not something this repo
tracks — keep your own compose override outside version control.

### Deploying to a Raspberry Pi over SSH

`bin/bootstrap-pi.sh` and `bin/deploy.sh` automate steps 1–2 above for a
Pi reachable over SSH, instead of running them by hand on the device:

1. **One-time:** `bin/bootstrap-pi.sh [--host <ssh-alias>]` installs
   Docker Engine + the Compose plugin if missing, and creates
   `/opt/renewvan/hub` (deployed compose artifacts), `/etc/renewvan`
   (host-specific config — Mosquitto broker config, tank calibration),
   and `/var/log/renewvan`. Defaults to the `renewvan` SSH host alias;
   set one up in `~/.ssh/config` first.
2. **Repeatable:** `bin/deploy.sh [--host <ssh-alias>]` rsyncs
   `docker-compose.yml` and `docker/` to `/opt/renewvan/hub`, then runs
   `docker compose pull && up -d` on the Pi. On a fresh Pi it seeds
   `.env` from `.env.example` (with `TANK_CONFIG_PATH` /
   `MOSQUITTO_CONFIG_PATH` already pointed at `/etc/renewvan/`) and
   exits asking you to fill in secrets and place
   `/etc/renewvan/tank/config.ini` before re-running — it never
   overwrites an existing `.env`.

## Architecture

See \[`/docs/[architecture.md](http://architecture.md)`\](docs/[architecture.md](http://architecture.md)) for the full layered

design (edge nodes → drivers → MQTT bus → application services →

presentation).

## Tips

### Rotating the display 180° (Official Raspberry Pi 7" Touchscreen v1)

Four independent layers must be aligned. Do them in order — each step is separate and non-redundant.

**1. Kernel & boot splash** — rotate at the KMS/DRM level so the boot splash, console, and touch coordinates all flip together from the earliest possible moment:

```
# /boot/firmware/cmdline.txt  (single line — no line breaks)
# Append to the existing line:
video=DSI-1:800x480M@60D,panel_orientation=upside_down
```

> `panel_orientation=upside_down` targets the DSI panel driver directly and is the only flag that rotates both the framebuffer and the early touch coordinate system in one step under `vc4-kms-v3d`. Legacy `lcd_rotate` / `display_rotate` do nothing on this driver.

**2. Wayland compositor** — leave the kanshi output at `normal` transform. The KMS layer already rotated the image; a compositor transform on top would double-flip it back:

```
# ~/.config/kanshi/config
profile {
  output DSI-1 transform normal
}
```

Reload without rebooting: `pkill -SIGHUP kanshi`

**3. Touch coordinates** — the ft5x06 controller does not read the KMS `panel_orientation` property; its raw evdev coordinates remain unrotated. Add a udev rule with a 180° libinput calibration matrix:

```
# /etc/udev/rules.d/99-ft5x06-rotation.rules
SUBSYSTEM=="input", ATTRS{name}=="10-0038 generic ft5x06 (00)", \
  ENV{LIBINPUT_CALIBRATION_MATRIX}="-1 0 1 0 -1 1"
```

Apply without rebooting:

```sh
sudo udevadm control --reload-rules
echo "10-0038" | sudo tee /sys/bus/i2c/drivers/edt_ft5x06/unbind
sleep 1
echo "10-0038" | sudo tee /sys/bus/i2c/drivers/edt_ft5x06/bind
```

**4. Mouse cursor** — hardware cursor overlays are exempt from `panel_orientation` and will appear unrotated. Force software rendering so the cursor is composited into the framebuffer and rotates with it:

```
# ~/.config/labwc/environment  (append one line)
WLR_NO_HARDWARE_CURSORS=1
```

Takes effect on next labwc session start (reboot or log out/in).

---

Reboot once after all four steps to confirm everything survives a cold start.
