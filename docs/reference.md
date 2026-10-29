# Hub reference

Operator reference for the renewvan hub: install, configuration, services, deployment, networking, and troubleshooting. This page is written to be published as-is on the documentation website. For the design, read [architecture.md](architecture.md). For domain vocabulary, read [`CONTEXT.md`](../CONTEXT.md). To automate deploys, driver scaffolding, and basemap builds with Claude Code, read [agent-skills.md](agent-skills.md).

Contents:

1. [Requirements](#requirements)
2. [Install](#install)
3. [Services](#services)
4. [Configuration](#configuration)
5. [Deploying to a Pi step by step](#deploying-to-a-pi-step-by-step)
6. [Offline map](#offline-map)
7. [HTTPS on the LAN](#https-on-the-lan)
8. [Broker security](#broker-security)
9. [Host plugins](#host-plugins)
10. [Touchscreen rotation](#touchscreen-rotation)
11. [Demo mode](#demo-mode)
12. [Validating schemas](#validating-schemas)
13. [Plain Docker Compose](#plain-docker-compose)
14. [Troubleshooting](#troubleshooting)

## Requirements

- A Raspberry Pi 4 or 5 running Raspberry Pi OS (Debian 13, 64-bit), reachable over `ssh` with a key.
- Your own machine (macOS or Linux) to run the installer from.
- Optional hardware, each enabling one driver: an ADS1115 tank sender board, a GY-521 tilt sensor, DS18B20 temperature probes, a u-blox USB GNSS receiver, a WattCycle BLE battery, an Autoterm AIR 2D heater, a Teltonika RUT router, a Venus OS device (Cerbo GX), an ESP32 16-channel relay board.

## Install

```bash
git clone https://github.com/renewvan/hub && cd hub
bin/install.sh --host <ssh-alias-or-user@pi>
```

The installer checks the Pi first, installs Docker, generates the InfluxDB credentials, starts the stack, and sets up the touchscreen. It is safe to re-run and never overwrites an existing `.env`. Reboot the Pi once afterwards to start the touchscreen session.

### Installer options

| Option                   | Effect                                                              |
| ------------------------ | ------------------------------------------------------------------- |
| `--venus-host <ip>`      | The van's Venus OS device (Cerbo GX etc.). Omit if there is none.   |
| `--venus-portal-id <id>` | Its VRM portal ID. Asked for if `--venus-host` is given without it. |
| `--public-host <h>`      | Address phones and laptops use to reach the Pi. Default: its IP.    |
| `--no-kiosk`             | Headless hub: skip the touchscreen session.                         |
| `--no-rotate`            | No 180° panel rotation; removes it if an earlier run added it.      |
| `--yes`                  | Never prompt; fail instead if something is missing.                 |

### Manual steps after install

Each of these needs a decision or a login only you can make:

- `sudo tailscale up` to join your tailnet (see [Host plugins](#host-plugins)).
- Tank calibration: set `sensor_min` and `sensor_max` in `/etc/renewvan/tank/config.ini`. It starts on the image's defaults.
- Tilt axis mapping, if the GY-521 is not mounted face-down (`/etc/renewvan/tilt/config.ini`).
- Broker authentication, which is **off** by default. Read [Broker security](#broker-security) before exposing the Pi beyond your own network.
- Flash the ESP32 relay driver once, per [`renewvan/driver-esp32`](https://github.com/renewvan/driver-esp32). Use release `v0.1.2` or later: `v0.1.1` does not build.
- Install the phone app per [`renewvan/mobile`](https://github.com/renewvan/mobile). It is not part of the compose flow.

## Services

Every service runs from a pinned, independently published image. Nothing is built from source in this repo ([ADR 0001](adr/0001-compose-services-via-pinned-images-not-git-submodules.md)). To bump a service, edit its `image:` tag in `docker-compose.yml`, the single git-tracked source of truth.

### Infrastructure and application services

| Service     | Image                        | Port                          | Purpose                                                                                     |
| ----------- | ---------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------- |
| `mosquitto` | `eclipse-mosquitto:2`        | 1883 (MQTT), 9001 (WebSocket) | The MQTT broker, the single source of truth.                                                |
| `influxdb`  | `influxdb:2`                 | 8086                          | History store (org and bucket `renewvan`).                                                  |
| `grafana`   | `grafana/grafana`            | 3000                          | History charts over InfluxDB, provisioned from code ([Grafana](#grafana)).                  |
| `logger`    | `ghcr.io/renewvan/logger`    | none                          | Writes one InfluxDB point per four-segment `renewvan/#` topic.                              |
| `dashboard` | `ghcr.io/renewvan/dashboard` | `127.0.0.1:80` (Pi only)      | The web app. The kiosk browser loads `http://localhost`.                                    |
| `caddy`     | `caddy:2`                    | 443 (LAN)                     | TLS terminator, the only LAN entry point for the dashboard. See [HTTPS](#https-on-the-lan). |

### Drivers

Drivers marked with a profile do not start by default, because without their hardware or a required setting they would abort or crash-loop the whole stack. `bin/deploy.sh` enables a profile when it detects the hardware or setting. For a manual start, add `--profile <name>`.

| Driver               | Profile     | Needs                                                                                        |
| -------------------- | ----------- | -------------------------------------------------------------------------------------------- |
| `driver-demo`        | none        | Nothing. Idle until Demo mode is switched on.                                                |
| `driver-router`      | none        | A Teltonika router publishing to `renewvan/router/<id>/raw`.                                 |
| `driver-tank`        | none        | ADS1115 on `/dev/i2c-1`.                                                                     |
| `driver-temperature` | none        | 1-Wire enabled (`dtoverlay=w1-gpio`) for DS18B20 probes. CPU sensor needs none.              |
| `driver-tilt`        | none        | GY-521 on `/dev/i2c-1`.                                                                      |
| `driver-gps`         | `gps`       | A u-blox USB receiver at `/dev/ttyACM0`.                                                     |
| `driver-autoterm`    | `autoterm`  | USB-serial adapter at `/dev/ttyAUTOTERM`. Commands stay off until `commands_enabled = true`. |
| `driver-venus`       | `venus`     | `VENUS_MQTT_HOST` and `VENUS_PORTAL_ID` in `.env`.                                           |
| `driver-wattcycle`   | `wattcycle` | A `mac` in its `config.ini`, and the battery in BLE range of the Pi.                         |

Example: `docker compose --profile gps --profile venus up -d`.

The ESP32 relay driver is firmware, not a container. It is flashed once from `renewvan/driver-esp32`.

### Volumes

Data persists in named Docker volumes: `mosquitto-data`, `mosquitto-log`, `influxdb-data`, `grafana-data`, `caddy-data`, `caddy-config`. Do not run `docker compose down -v` casually: `caddy-data` holds the TLS root every phone trusts.

## Grafana

Grafana is for history: charts over what the `logger` wrote to InfluxDB. It is separate from the live dashboard and is not on the kiosk. Open `http://<pi-ip>:3000`.

**Login.** First login is `admin` / `admin`; Grafana then asks you to set a new password. Do that straight away, because port 3000 is open on the LAN. The password lives in the `grafana-data` volume, so it survives redeploys.

**After a deploy.** An existing install keeps the admin password already set in the `grafana-data` volume, so `admin` / `admin` only works on a fresh volume. Log in, open Connections → Data sources → InfluxDB and press Save & test: it should say "datasource is working". Then open Dashboards → `renewvan` → Van overview. The tank time series is empty when no tank value changed within the selected range; the gauges still show the last value.

**Datasource.** `InfluxDB` (Flux, `http://influxdb:8086`, org and bucket `renewvan`) is provisioned from `docker/grafana/provisioning/datasources/influxdb.yml`. Its token is `INFLUXDB_ADMIN_TOKEN`, passed to the container by compose, so nothing secret is in git. Check it under Connections → Data sources → InfluxDB → Save & test. If you change the token in `.env`, re-run `bin/deploy.sh`, which restarts Grafana.

**Dashboard.** "Van overview" (folder `renewvan`, default range last 6 hours, refresh 30 s) comes from `docker/grafana/dashboards/van-overview.json`: current tank and battery gauges and solar power, a loads block (below), then time series for tank levels, battery state of charge, battery voltage, solar power and battery temperature. It only charts what the logger stores: `tank`, `relay`, `battery`, `solar`, `charger` and `router` measurements, tagged by `id`. Temperature probes and tilt are not logged yet, so there is no panel for them.

**Loads (power consumption).** No entity reports consumption, so the dashboard derives it from the energy balance: loads = solar power + charger power − battery net power (`battery.power_w` is positive while charging). It gives "Power balance" (solar, battery and loads over time), "Loads now", "Energy used in range", "Energy used per hour" in Wh, and a "Relay state" timeline of when each relay was on. Things to know:

- Pick the battery in the **Battery** dropdown at the top. A bank seen by two drivers (for example a Venus shunt and a BMS) appears as two ids, and only the one you choose is used, so nothing is counted twice. Choose the one that shares a bank with the solar controller.
- The loads figure is an estimate. It includes inverter and conversion losses and misses any power source the logger does not store, such as an alternator charger with no `charger` entity.
- Relay state is the latched command, not measured current ([CONTEXT.md](../CONTEXT.md), Relay). The timeline, like the tank series, is empty when no relay changed within the selected range.
- To see consumption per circuit you need a current sensor per load; the hub has no such entity yet.

**Add a panel.** Provisioned dashboards are read-only in the UI, so changes go through the file:

1. Open the dashboard, choose Edit → Save as to make a scratch copy, and build the panel there. A query looks like `from(bucket: "renewvan") |> range(start: v.timeRangeStart, stop: v.timeRangeStop) |> filter(fn: (r) => r._measurement == "battery" and r._field == "power_w")`. The measurement is the entity, the field is the topic's last segment, and `id` is its third.
2. Copy the panel JSON (panel menu → Inspect → Panel JSON) into `panels` in `van-overview.json`, or export the whole dashboard (Share → Export → Export as JSON) and replace the file.
3. Commit it and run `bin/deploy.sh`. Grafana reloads dashboard files every 30 seconds.

To add a new dashboard, drop another JSON file in `docker/grafana/dashboards/`.

## Configuration

Copy `.env.example` to `.env`. The installer does this for you. `.env.example` documents every value in comments; this table summarizes them.

| Variable                                                | Default                              | Purpose                                                                                                                                  |
| ------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `INFLUXDB_ADMIN_PASSWORD`                               | `renewvan-dev-only`                  | InfluxDB admin password. Change it; the installer generates one.                                                                         |
| `INFLUXDB_ADMIN_TOKEN`                                  | `renewvan-dev-token`                 | InfluxDB admin token. Change it; the installer generates one.                                                                            |
| `RENEWVAN_HOST` / `RENEWVAN_PORT`                       | `mosquitto` / `1883`                 | Broker address for services that read it from the environment.                                                                           |
| `RENEWVAN_USERNAME` / `RENEWVAN_PASSWORD`               | empty                                | Broker credentials, if you turn authentication on. See [Broker security](#broker-security).                                              |
| `MOSQUITTO_CONFIG_PATH`                                 | `./docker/mosquitto/config`          | Mosquitto config directory. `bin/deploy.sh` points it at `/etc/renewvan/mosquitto/`.                                                     |
| `TANK_CONFIG_PATH`                                      | `./config/tank/config.ini`           | Tank driver config (calibration lives here). Must exist before first start.                                                              |
| `GPS_CONFIG_PATH`                                       | `./config/gps/config.ini`            | GPS driver config.                                                                                                                       |
| `TEMPERATURE_CONFIG_PATH`, `TEMPERATURE_STATE_PATH`     | `./config/temperature/…`             | Config, and a writable directory for names and units edited in the dashboard.                                                            |
| `TILT_CONFIG_PATH`, `TILT_STATE_PATH`                   | `./config/tilt/…`                    | Config (axis mapping), and a writable directory holding the install level zero.                                                          |
| `WATTCYCLE_CONFIG_PATH`                                 | `./config/wattcycle/config.ini`      | WattCycle config. The driver stays off until `mac` is set.                                                                               |
| `AUTOTERM_CONFIG_PATH`                                  | `./config/autoterm/config.ini`       | Autoterm heater config.                                                                                                                  |
| `VENUS_MQTT_HOST`, `VENUS_MQTT_PORT`, `VENUS_PORTAL_ID` | empty / `1883` / empty               | The Venus OS device's own broker. Leave the host empty and `driver-venus` is not started.                                                |
| `ROUTER_ID`                                             | `main`                               | The `<id>` segment of `renewvan/router/<id>/…`. One router per hub.                                                                      |
| `VITE_MQTT_WS_URL`                                      | `/mqtt`                              | How browsers reach the broker's WebSocket. `/mqtt` means the page's own host, proxied by the dashboard. Needs dashboard v0.7.1 or later. |
| `VITE_MQTT_USERNAME`, `VITE_MQTT_PASSWORD`              | empty                                | Browser-side broker credentials.                                                                                                         |
| `DASHBOARD_BASEMAP_PATH`                                | `./config/dashboard/basemap.pmtiles` | The offline map file. `bin/deploy.sh` uses `/etc/renewvan/dashboard/basemap.pmtiles`.                                                    |
| `DASHBOARD_HOST`                                        | empty, **required**                  | The Pi's LAN address or name. Compose requires it for every service; deploy refuses to run while it is empty.                            |

## Deploying to a Pi step by step

`bin/install.sh` calls these scripts. Run them one at a time if you want control over one step. `--host` defaults to the `renewvan` SSH alias, so set that up in `~/.ssh/config` first.

1. **One-time:** `bin/bootstrap-pi.sh [--host <ssh-alias>]`. Installs Docker Engine and the Compose plugin if missing. Creates `/opt/renewvan/hub` (deployed compose artifacts), `/etc/renewvan` (host config: Mosquitto, driver configs), and `/var/log/renewvan`.
2. **Repeatable:** `bin/deploy.sh [--host <ssh-alias>]`. Rsyncs `docker-compose.yml` and `docker/` to `/opt/renewvan/hub`, seeds each driver's `/etc/renewvan/<driver>/config.ini` from `docker/<driver>/` if missing, then runs `docker compose pull && up -d`. On a fresh Pi it seeds `.env` from `.env.example` and exits asking you to fill in the secrets (`INFLUXDB_ADMIN_PASSWORD`, `INFLUXDB_ADMIN_TOKEN`, `VITE_MQTT_WS_URL`, `DASHBOARD_HOST`, and `VENUS_*` if the van has Venus OS). It never overwrites an existing `.env`.
3. **Once, for the touchscreen:** `bin/bootstrap-kiosk-pi.sh [--host <ssh-alias>]`. See [`plugins/kiosk/README.md`](../plugins/kiosk/README.md).

To upgrade, edit the `image:` tag in `docker-compose.yml` and run `bin/deploy.sh`. To roll back, restore the previous tag and run it again.

## Offline map

The dashboard's Map page draws one offline file, the **Basemap**. A fresh install has none, so the Map page shows its "no map" state.

- `DASHBOARD_BASEMAP_PATH` in `.env` is mounted read-only into the dashboard.
- `bin/deploy.sh` creates an empty placeholder only if no file exists. A redeploy never overwrites your map.
- `scripts/push-basemap.sh <file> [--host <ssh-alias>]` replaces it safely from your own machine: it verifies the file, checks free space, swaps it in atomically, restarts the dashboard, and confirms it serves. It needs the `pmtiles` CLI locally. The smoke procedure is in [`scripts/README.md`](../scripts/README.md).

To build a Basemap, see the [dashboard's offline-map guide](https://github.com/renewvan/dashboard/blob/main/docs/offline-map.md).

## HTTPS on the LAN

The dashboard is served over HTTPS on the LAN so a phone can install it as a PWA. Caddy terminates TLS with its own local CA (`tls internal`). Nothing is exposed to the internet, so do not port-forward 443 on your router.

Published ports:

- `caddy`: host port 443 (LAN).
- `dashboard`: `127.0.0.1:80` only, for the kiosk browser (`http://localhost`).

### Setup

1. Keep a `renewvan/dashboard` checkout next to this repo (`../dashboard`). `bin/deploy.sh` ships its `docker/Caddyfile` to the Pi, and compose mounts it from there.
2. On the Pi, set `DASHBOARD_HOST` in `/opt/renewvan/hub/.env` to its LAN address (`hostname -I | awk '{print $1}'`).
3. Run `bin/deploy.sh`. It restarts Caddy, because the Caddyfile is a single-file bind mount that a redeploy does not reload.

### Trust the root on each phone (once)

Caddy's root CA is in the `caddy-data` volume. Run these on the Pi in `/opt/renewvan/hub`, then copy `renewvan-root.cer` off it. The DER form is what Android installs directly:

```bash
docker cp renewvan-caddy:/data/caddy/pki/authorities/local/root.crt ./renewvan-root.crt
openssl x509 -in renewvan-root.crt -outform DER -out renewvan-root.cer
openssl x509 -in renewvan-root.cer -inform DER -noout -fingerprint -sha256
```

Copy `renewvan-root.cer` to the phone over a channel you trust (USB, email, or a cloud drive). Do not serve it from a long-lived LAN server.

On Android (verified with Chrome):

1. Settings → Security → Encryption & credentials → Install a certificate → **CA certificate**, then pick the file.
2. Check it under Trusted credentials → **User**. Its subject is `Caddy Local Authority - 2026 ECC Root`, and its SHA-256 matches the command above.
3. Close Chrome completely, reopen it, and load `https://<pi-lan-ip>`.

Android may warn "Network may be monitored" after you install a user CA. That is expected. iOS has not been tested with this setup.

### Certificate lifetime

The root is valid for 10 years. Caddy signs the server certificate directly with it (`sign_with_root`) for 90 days and renews it automatically, so renewals need no reinstall. Never delete the `caddy-data` volume: every device would have to trust a new root.

## Broker security

The repo's Mosquitto config runs `allow_anonymous true`. That is fine on a private van network and wrong anywhere else. Before exposing the Pi, know that turning on authentication takes more than setting `RENEWVAN_USERNAME` and `RENEWVAN_PASSWORD`, because not every client reads them:

| Client                                                                               | Where it reads its credential                            |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------- |
| `logger`, `driver-venus`, `driver-router`, `driver-demo`                             | `MQTT_USERNAME`/`MQTT_PASSWORD`, filled by compose       |
| `driver-tank`, `driver-gps`, `driver-temperature`, `driver-tilt`, `driver-wattcycle` | `[mqtt]` section of `/etc/renewvan/<driver>/config.ini`  |
| Host plugins (kiosk, tailscale)                                                      | `/etc/renewvan/kiosk.env`, `/etc/renewvan/tailscale.env` |
| ESP32 relay                                                                          | its `secrets.yaml`                                       |
| Browsers                                                                             | `VITE_MQTT_USERNAME`/`VITE_MQTT_PASSWORD`                |

Give every client its credential first, then require authentication, and keep the old `mosquitto.conf` to roll back to. Locking the broker with only the environment pair set takes the five config-driven drivers offline (`MQTT connect failed with rc=5`). The open design is tracked in hub issue 49.

## Host plugins

Plugins under `plugins/` are Pi-level systemd services that run outside Docker.

| Plugin              | What it does                                                                           | Docs                                                                            |
| ------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `plugins/hub`       | Publishes the Pi's LAN address and the hub version; holds the primary-battery setting. | [README](../plugins/hub/README.md)                                              |
| `plugins/kiosk`     | Display power, brightness, and idle auto-sleep for the touchscreen.                    | [README](../plugins/kiosk/README.md), [sleep/wake](kiosk-display-sleep-wake.md) |
| `plugins/tailscale` | Installs Tailscale and publishes VPN status. Active after `sudo tailscale up`.         | [README](../plugins/tailscale/README.md)                                        |

Remote access uses Tailscale, with no port forwarding or public IP. Note that the HTTPS certificate only covers `DASHBOARD_HOST`, so use the `http` path or the MQTT-WS proxy when connecting over a Tailscale address.

## Touchscreen rotation

For the Official Raspberry Pi 7" Touchscreen v1. `bin/bootstrap-kiosk-pi.sh` applies all of this; skip it with `--no-rotate`. Rotation is done once, in the kernel; cage has no output transform, so nothing in the compositor can double-flip it. Two layers must agree.

**1. Kernel and boot splash.** Rotate at the KMS/DRM level so the splash, console, and framebuffer flip together:

```text
# /boot/firmware/cmdline.txt  (single line, no line breaks)
# Append to the existing line:
video=DSI-1:800x480M@60D,panel_orientation=upside_down
```

`panel_orientation=upside_down` targets the DSI panel driver directly and is the only flag that rotates both the framebuffer and the early touch coordinate system under `vc4-kms-v3d`. Legacy `lcd_rotate` and `display_rotate` do nothing on this driver.

**2. Touch coordinates.** The ft5x06 controller does not read `panel_orientation`, so its raw coordinates stay unrotated. Add a udev rule with a 180° libinput calibration matrix:

```text
# /etc/udev/rules.d/99-ft5x06-rotation.rules
SUBSYSTEM=="input", ATTRS{name}=="10-0038 generic ft5x06 (00)", \
  ENV{LIBINPUT_CALIBRATION_MATRIX}="-1 0 1 0 -1 1"
```

Apply without rebooting:

```bash
sudo udevadm control --reload-rules
echo "10-0038" | sudo tee /sys/bus/i2c/drivers/edt_ft5x06/unbind
sleep 1
echo "10-0038" | sudo tee /sys/bus/i2c/drivers/edt_ft5x06/bind
```

The mouse cursor is a hardware overlay exempt from `panel_orientation`, so it would appear unrotated. `renewvan-cage.service` sets `WLR_NO_HARDWARE_CURSORS=1` to composite it into the framebuffer instead. Reboot once to confirm everything survives a cold start.

## Demo mode

To try the dashboard without a van, switch on Settings → General → System → Demo mode in any dashboard. `driver-demo` is part of the stack and idle until asked. The switch is shared on the bus (`renewvan/demo/enabled`), so every client then shows a looping simulated van published under the `renewvan/demo/` namespace. Real drivers and the logger keep running alongside it; nothing real is overwritten and nothing simulated is stored. Full contract: [demo-mode.md](demo-mode.md).

## Validating schemas

Run these after any schema change:

- `./schema/validate-examples.sh` checks the fixtures in `schema/examples/` against each schema: `*.valid.json` must pass and `*.invalid.json` must fail. It also fails when an entity schema is missing from the `publishes` enum in `driver-manifest.schema.json` and `driver-info.schema.json`, so adding an entity means adding it to both.
- `python3 schema/validate-demo.py` (needs `pip install jsonschema`) checks every demo scenario in `schema/demo/` and the simulated fleet. Every entity needs a scenario.

To add a driver, see [authoring-a-driver.md](authoring-a-driver.md). To port a Venus OS dbus driver, see [porting-dbus-to-mqtt-driver.md](porting-dbus-to-mqtt-driver.md).

## Plain Docker Compose

`docker-compose.yml` is the plain-Compose-host path with named volumes, for any Linux machine running Docker:

```bash
cp .env.example .env     # fill in DASHBOARD_HOST and the secrets
docker compose pull
docker compose up -d
```

Hosts that need bind-mounted data directories (for example a NAS app platform) should keep their own compose override outside version control. This repo does not track any platform-specific compose file.

## Troubleshooting

| Symptom                                         | Cause and fix                                                                                                            |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `NET::ERR_CERT_AUTHORITY_INVALID`               | Root not installed, installed under the wrong category (for example VPN & app user certificate), or the wrong file.      |
| `NET::ERR_CERT_COMMON_NAME_INVALID`             | The URL is not `DASHBOARD_HOST`. The certificate covers only that address; a Tailscale or public address will not match. |
| `NET::ERR_CERT_DATE_INVALID`                    | The phone's clock is wrong.                                                                                              |
| `docker compose up` fails with a missing device | A driver with a `devices:` entry started without its hardware. Leave its profile off, or run `bin/deploy.sh`.            |
| `MQTT connect failed with rc=5` in a driver     | The broker requires auth and that driver has no credential in its `config.ini`. See [Broker security](#broker-security). |
| Map page says "Offline map unavailable"         | No Basemap installed. See [Offline map](#offline-map).                                                                   |
| `driver-venus` restarts forever                 | `VENUS_MQTT_HOST` or `VENUS_PORTAL_ID` is empty. Fill them in, or leave the `venus` profile off.                         |
| Compose complains `DASHBOARD_HOST` is unset     | Set it in `.env` to the Pi's LAN address.                                                                                |
