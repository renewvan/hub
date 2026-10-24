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

ESP32 relay driver over ESPHome, normalizes into the same schema and

appears on the same bus.

The goal isn't to replace any of these platforms — it's to be the open,

vendor-agnostic glue between them.

## Status

Early / hobby-scale (v0). This repo (`hub`) hosts the shared device-model
schema, the MQTT/InfluxDB/Grafana deployment (docker-compose), and
project docs. Drivers, bridges, the logger, and the dashboard each live
in their own repo under the `renewvan` GitHub org (`driver-tank`, `driver-venus`,
`logger`) — see `.scratch/renewvan-hub-v0-build/` for the v0 build spec.
Not yet ready for production van use — see \[Roadmap\](#roadmap).

## Install

From your own machine (macOS or Linux), with a Raspberry Pi running Raspberry Pi OS (Debian 13, 64-bit)
that you can `ssh` into with a key:

```bash
git clone https://github.com/renewvan/hub && cd hub
bin/install.sh --host <ssh-alias-or-user@pi>
```

That is the whole install. It checks the Pi first, installs Docker, generates the InfluxDB
credentials, starts the stack, and sets up the touchscreen. It is safe to re-run, and it never
overwrites an existing `.env`.

| Option                   | Effect                                                              |
| ------------------------ | ------------------------------------------------------------------- |
| `--venus-host <ip>`      | The van's Venus OS device (Cerbo GX etc.). Omit if there is none.   |
| `--venus-portal-id <id>` | Its VRM portal ID. Asked for if `--venus-host` is given without it. |
| `--public-host <h>`      | Address phones and laptops use to reach the Pi. Default: its IP.    |
| `--no-kiosk`             | Headless hub: skip the touchscreen session.                         |
| `--no-rotate`            | Skip the 180° panel rotation.                                       |
| `--yes`                  | Never prompt; fail instead if something is missing.                 |

Reboot the Pi once afterwards to start the touchscreen session.

Still manual, because each needs a decision or a login only you can make: `sudo tailscale up`;
tank calibration (`sensor_min`/`sensor_max` in `/etc/renewvan/tank/config.ini`, which starts on the
image's defaults); the tilt axis mapping if the GY-521 is not mounted face-down; and broker
authentication, which is **off** by default (see `.env.example` and hub issue 49 before exposing the
Pi beyond your own network). The ESP32 relay driver (`renewvan/driver-esp32`) and the phone app
(`renewvan/mobile`) are flashed and installed per their own READMEs.

### Offline map

The dashboard's Map page draws one offline file, the **Basemap**. A fresh install has none, so the
Map page shows its "no map" state. The hub's part is small: `DASHBOARD_BASEMAP_PATH` in `.env`
(default `/etc/renewvan/dashboard/basemap.pmtiles`) is mounted read-only into the dashboard,
`bin/deploy.sh` creates an empty placeholder there only if no file exists (a redeploy never
overwrites your map), and `scripts/push-basemap.sh <file> [--host <ssh-alias>]` replaces it safely
from your own machine (verifies the file, checks free space, swaps it in atomically, restarts the
dashboard, confirms it serves). How to build a Basemap is in the
[dashboard's offline-map guide](https://github.com/renewvan/dashboard/blob/main/docs/offline-map.md);
smoke procedure for the script: `scripts/README.md`.

### Without the installer

`bin/install.sh` runs the pieces below in order; run them yourself if you want control over one:

1. **Copy `.env.example` to `.env`** and fill in the required values (the dashboard's
   browser-reachable MQTT-WS URL; Venus OS device reachability, if the van has one) — see
   `.env.example`'s comments for what each value is and where it's used.
2. **Pull and start everything:** `docker compose pull && docker compose up -d`. This brings up the
   infra layer (Mosquitto, InfluxDB, Grafana) alongside `driver-tank`, `logger`, and `dashboard` —
   each a pinned, independently-published image (own repo, own release; nothing built from source in
   `hub`, per `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`). Bumping a
   service later is a single `image:` tag edit in `docker-compose.yml`. Two drivers are behind compose
   profiles because they cannot start without their hardware or a required setting, and would
   otherwise abort or crash-loop the whole stack: `driver-gps` (`gps`, needs `/dev/ttyACM0`) and
   `driver-venus` (`venus`, needs `VENUS_MQTT_HOST` and `VENUS_PORTAL_ID`). Add `--profile gps` /
   `--profile venus`, or let `bin/deploy.sh` decide from the device and `.env`.
3. **Flash the ESP32 16-channel relay driver**, once, per `renewvan/driver-esp32`'s own README
   (ESPHome YAML, no custom firmware). Use release `v0.1.2` or later: `v0.1.1` does not build.
4. **Install the phone app** per `renewvan/mobile`'s own README (React Native — sideload or
   app-store build, not part of this compose flow).

`docker-compose.yml` is the plain-Compose-host path (named volumes). A
host-path/bind-mount deployment (e.g. TrueNAS's Custom App "Install via
YAML") is a personal deployment concern, not something this repo
tracks — keep your own compose override outside version control.

### Demo mode

To try the dashboard without a van, turn on Settings → General → System → Demo mode in any
dashboard. `driver-demo` is part of the compose stack and idle until asked; the switch is shared on
the bus (`renewvan/demo/enabled`), so every client then shows a looping simulated van published
under the `renewvan/demo/` namespace (see `docs/demo-mode.md`). Real drivers and the logger keep
running alongside it; nothing real is overwritten and nothing simulated is ever stored. Writing a driver for an entity that already
exists? You ship no demo: see `docs/porting-dbus-to-mqtt-driver.md` ("Adding a driver for an
existing entity").

### Validating schemas

- `./schema/validate-examples.sh` checks the example fixtures in `schema/examples/` against each
  schema (entities, plus `driver-manifest` and `driver-info`, the contract for a Driver's `manifest.json`
  and its retained `renewvan/<driver>/info` payload). It also fails when an entity schema exists that
  the `publishes` enum in `driver-manifest.schema.json` and `driver-info.schema.json` does not list, so
  adding an entity means adding it to both enums.
- `python3 schema/validate-demo.py` (needs `pip install jsonschema`) checks the demo scenarios in
  `schema/demo/`; every entity needs one.

### Deploying to a Raspberry Pi over SSH, step by step

The scripts `bin/install.sh` calls, for running one at a time against a Pi reachable over SSH:

1. **One-time:** `bin/bootstrap-pi.sh [--host <ssh-alias>]` installs Docker Engine + the Compose
   plugin if missing, and creates `/opt/renewvan/hub` (deployed compose artifacts), `/etc/renewvan`
   (host-specific config — Mosquitto broker config, driver configs), and `/var/log/renewvan`.
   Defaults to the `renewvan` SSH host alias; set one up in `~/.ssh/config` first.
2. **Repeatable:** `bin/deploy.sh [--host <ssh-alias>]` rsyncs `docker-compose.yml` and `docker/` to
   `/opt/renewvan/hub`, seeds each driver's `/etc/renewvan/<driver>/config.ini` from `docker/<driver>/`
   if missing, then runs `docker compose pull && up -d` on the Pi. On a fresh Pi it seeds `.env`
   from `.env.example` and exits asking you to fill in the secrets (`INFLUXDB_ADMIN_PASSWORD`,
   `INFLUXDB_ADMIN_TOKEN`, `VITE_MQTT_WS_URL`, and `VENUS_*` if the van has Venus OS) before
   re-running — it never overwrites an existing `.env`. `bin/install.sh` writes that `.env` for
   you first, with generated credentials.
3. **Once, for the touchscreen:** `bin/bootstrap-kiosk-pi.sh [--host <ssh-alias>]`. See
   `plugins/kiosk/README.md`.

### HTTPS for the dashboard on the LAN

The kiosk dashboard is served over HTTPS on the LAN so a phone can install it as a PWA. Caddy terminates TLS with its own local CA (`tls internal`). Nothing is exposed to the internet, so don't port-forward 443 on your router.

Published ports:

+- `caddy`: host port 443 (LAN).
+- `dashboard`: `127.0.0.1:80` only, for the kiosk browser (`http://localhost`).

**Setup**

1. Keep a `renewvan/dashboard` checkout next to this repo (`../dashboard`). `bin/deploy.sh` ships its `docker/Caddyfile` to the Pi, and compose mounts it from there.
2. On the Pi, set `DASHBOARD_HOST` in `/opt/renewvan/hub/.env` to its LAN address (`hostname -I | awk '{print $1}'`). Deploy refuses to run while it's empty, because compose requires it for every service.
3. Run `bin/deploy.sh`. It restarts Caddy, because the Caddyfile is a single-file bind mount that a redeploy doesn't reload.

**Trust the root on each phone (once)**

Caddy's root CA is in the `caddy-data` volume. Run these on the Pi in `/opt/renewvan/hub`, then copy `renewvan-root.cer` off it. The DER form is what Android installs directly:

```
docker cp renewvan-caddy:/data/caddy/pki/authorities/local/root.crt ./renewvan-root.crt
openssl x509 -in renewvan-root.crt -outform DER -out renewvan-root.cer
openssl x509 -in renewvan-root.cer -inform DER -noout -fingerprint -sha256
```

Copy `renewvan-root.cer` to the phone over a channel you trust (USB, email, or a cloud drive). Don't serve it from a long-lived LAN server.

On Android (verified with Chrome):

1. Settings → Security → Encryption & credentials → Install a certificate → **CA certificate**, then pick the file.
2. Check it under Trusted credentials → **User**. Its subject is `Caddy Local Authority - 2026 ECC Root`, and its SHA-256 matches the command above.
3. Close Chrome completely, reopen it, and load `https://<pi-lan-ip>`.

Android may warn "Network may be monitored" after you install a user CA. That is expected. iOS hasn't been tested with this setup.

**Certificate lifetime**

The root is valid for 10 years. Caddy signs the server certificate directly with it (`sign_with_root`) for 90 days and renews it automatically, so renewals need no reinstall. Never delete the `caddy-data` volume or run `docker compose down -v`: it holds the root, and every device would have to trust a new one.

**Troubleshooting**

| Chrome message                                                          | Cause                                                                                                                   |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Server's certificate is not trusted (`NET::ERR_CERT_AUTHORITY_INVALID`) | Root not installed, installed under the wrong category (for example VPN & app user certificate), or the wrong file.     |
| `NET::ERR_CERT_COMMON_NAME_INVALID`                                     | The URL isn't the LAN address. The certificate covers only `DASHBOARD_HOST`; a Tailscale or public address won't match. |
| `NET::ERR_CERT_DATE_INVALID`                                            | The phone's clock is wrong.                                                                                             |

## Architecture

See \[`/docs/[architecture.md](http://architecture.md)`\](docs/[architecture.md](http://architecture.md)) for the full layered

design (edge devices → drivers → MQTT bus → application services →

presentation).

## Tips

### Rotating the display 180° (Official Raspberry Pi 7" Touchscreen v1)

`bin/bootstrap-kiosk-pi.sh` applies all of this (skip with `--no-rotate`). Rotation is
done once, in the kernel; cage has no output transform, so nothing in the compositor
can double-flip it. Two layers must agree:

**1. Kernel & boot splash** — rotate at the KMS/DRM level so the boot splash, console and framebuffer flip together from the earliest possible moment:

```
# /boot/firmware/cmdline.txt  (single line — no line breaks)
# Append to the existing line:
video=DSI-1:800x480M@60D,panel_orientation=upside_down
```

> `panel_orientation=upside_down` targets the DSI panel driver directly and is the only flag that rotates both the framebuffer and the early touch coordinate system in one step under `vc4-kms-v3d`. Legacy `lcd_rotate` / `display_rotate` do nothing on this driver.

**2. Touch coordinates** — the ft5x06 controller does not read the KMS `panel_orientation` property; its raw evdev coordinates remain unrotated. Add a udev rule with a 180° libinput calibration matrix:

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

The mouse cursor is a hardware overlay exempt from `panel_orientation`, so it would
appear unrotated. `renewvan-cage.service` sets `WLR_NO_HARDWARE_CURSORS=1` to
composite it into the framebuffer instead.

Reboot once to confirm everything survives a cold start.
