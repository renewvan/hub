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
- Optional hardware, each enabling one driver: an ADS1115 tank sender board, a GY-521 tilt sensor, DS18B20 temperature probes, a u-blox USB GNSS receiver, a WattCycle BLE battery, an Autoterm AIR 2D heater, a Teltonika RUT router, a Venus OS device (Cerbo GX) or Victron devices on VE.Direct USB cables, an ESP32 16-channel relay board.

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

Every service runs from a pinned, independently published image. Nothing is built from source in this repo ([ADR 0001](adr/0001-compose-services-via-pinned-images-not-git-submodules.md)). To bump a service, edit its `image:` line in `docker-compose.yml`, the single git-tracked source of truth. The third-party infra images (`eclipse-mosquitto`, `influxdb`, `caddy`, `grafana`) are pinned as exact version plus digest (`image: name:X.Y.Z@sha256:...`); the `renewvan/*` images as an exact version tag. Renovate opens one PR per bump and a human merges it; `bin/test_pinning.py` fails CI on a floating tag, an unpinned Action or a `curl | sh`.

### Infrastructure and application services

| Service     | Image                        | Port                          | Purpose                                                                                     |
| ----------- | ---------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------- |
| `mosquitto` | `eclipse-mosquitto`          | 1883 (MQTT), 9001 (WebSocket) | The MQTT broker, the single source of truth.                                                |
| `influxdb`  | `influxdb`                   | `127.0.0.1:8086` (Pi only)    | History store (org and bucket `renewvan`).                                                  |
| `grafana`   | `grafana/grafana`            | `127.0.0.1:3000` (Pi only)    | History charts over InfluxDB, provisioned from code ([Grafana](#grafana)).                  |
| `logger`    | `ghcr.io/renewvan/logger`    | none                          | Writes one InfluxDB point per four-segment `renewvan/#` topic.                              |
| `dashboard` | `ghcr.io/renewvan/dashboard` | `127.0.0.1:80` (Pi only)      | The web app. The kiosk browser loads `http://localhost`.                                    |
| `caddy`     | `caddy`                      | 443 (LAN)                     | TLS terminator, the only LAN entry point for the dashboard. See [HTTPS](#https-on-the-lan). |

### Drivers

Drivers marked with a profile do not start by default, because without their hardware or a required setting they would abort or crash-loop the whole stack. `bin/deploy.sh` enables a profile when it detects the hardware or setting. For a manual start, add `--profile <name>`.

| Driver               | Profile     | Needs                                                                                                                                                                                                                                    |
| -------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `driver-demo`        | none        | Nothing. Idle until Demo mode is switched on.                                                                                                                                                                                            |
| `driver-router`      | none        | A Teltonika router publishing to `renewvan/router/<id>/raw`.                                                                                                                                                                             |
| `driver-tank`        | none        | ADS1115 on `/dev/i2c-1`.                                                                                                                                                                                                                 |
| `driver-temperature` | none        | 1-Wire enabled (`dtoverlay=w1-gpio`) for DS18B20 probes. CPU sensor needs none.                                                                                                                                                          |
| `driver-tilt`        | none        | GY-521 on `/dev/i2c-1`.                                                                                                                                                                                                                  |
| `driver-gps`         | `gps`       | A u-blox USB receiver at `/dev/ttyACM0`.                                                                                                                                                                                                 |
| `driver-autoterm`    | `autoterm`  | USB-serial adapter at `/dev/ttyAUTOTERM`. Commands stay off until `commands_enabled = true`.                                                                                                                                             |
| `driver-venus`       | `venus`     | `VENUS_MQTT_HOST` and `VENUS_PORTAL_ID` in `.env`.                                                                                                                                                                                       |
| `driver-vedirect`    | `vedirect`  | A Victron VE.Direct USB cable: a `/dev/serial/by-id` entry matching `port_globs` (default `usb-Duppa_srl_*, usb-VictronEnergy_*, usb-FTDI_*`; `bin/vedirect-gate.sh` applies the same globs, or a `port_globs` set in its `config.ini`). |
| `driver-wattcycle`   | `wattcycle` | A `mac` in its `config.ini`, and the battery in BLE range of the Pi.                                                                                                                                                                     |

Example: `docker compose --profile gps --profile venus up -d`.

**Heater adapter (`/dev/ttyAUTOTERM`).** Plug the heater's FTDI USB-serial adapter into the Pi, then run from your machine:

```bash
bin/bootstrap-pi.sh --with-heater
```

It finds the adapter's serial (and asks which one if several are plugged in), writes `/etc/udev/rules.d/99-renewvan-autoterm.rules`, reloads udev (also on a re-run if `/dev/ttyAUTOTERM` is missing) and adds the login user to `dialout` (so that user can open the device for debugging; the driver container gets it through its compose `devices:` entry). It refuses adapters with no serial or duplicate serials, and warns if the symlink does not appear. It is safe to re-run; run it again to point the symlink at a replacement adapter. Log in again for the `dialout` membership to apply, then re-run `bin/deploy.sh` to enable the `autoterm` profile.

**Swapping the adapter.** A replacement adapter has a different serial, so the old rule no longer matches. Plug the new one in (unplug the old one first, so only one FTDI adapter is present) and re-run `bin/bootstrap-pi.sh --with-heater`; it rewrites the rule. If `bin/deploy.sh` finds no `/dev/ttyAUTOTERM`, it skips the `autoterm` profile, still exits 0, prints this command and, when exactly one FTDI adapter is not yet named by the rule, shows its serial. Deploy never installs rules itself.

**Manual rule, without the script.** Find the adapter's serial with `udevadm info -a -n /dev/ttyUSB0 | grep 'ATTRS{serial}' | head -1`, then write `/etc/udev/rules.d/99-renewvan-autoterm.rules` as one line, with your serial:

```text
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", ATTRS{serial}=="<serial>", SYMLINK+="ttyAUTOTERM", GROUP="dialout", MODE="0660"
```

Reload with `sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty --action=add`, check that `/dev/ttyAUTOTERM` exists, and re-run `bin/deploy.sh`.

### Enabling heater commands

Commands (start, stop, setpoint) are off until `commands_enabled = true`, because the broker is anonymous and a start command can run a diesel heater. Work through this list in order, with someone at the van.

Preconditions:

- [ ] The broker is on the van LAN only, and you ran the checks in [Broker reachability](#broker-reachability): both ports unreachable from the uplink side.
- [ ] The Driver is deployed and `/dev/ttyAUTOTERM` exists.

Verify on the real heater:

- [ ] Early stop (`1.x` and `2.x` status codes) behaves safely: a human is present and the exhaust is clear.
- [ ] A live setpoint or power-level change is confirmed by the next `0x02` read, checked by a human at the van.
- [ ] The Driver's status-to-phase mapping, unknown-code rule and staleness clearing have run against the real heater.

Flip the switch:

- [ ] Set `commands_enabled = true` in `/etc/renewvan/autoterm/config.ini`, then `docker compose restart driver-autoterm`, with someone at the van.
- [ ] Send the first command by hand with `mosquitto_pub`, not the dashboard: start, watch `phase` follow, then stop.

To turn commands off, set `commands_enabled = false` and restart. Removing the container never starts or stops the heater: its own manual off is the authority.

The Driver refuses `ventilating/set` (the `3.35` status needs a bench check) and `clear_error/set` until they are verified, and never exposes `0x0D`.

The Heater page must ask for confirmation before a start, make stop a single tap, and show a pending state that times out honestly rather than assuming the command worked.

The ESP32 relay driver is firmware, not a container. It is flashed once from `renewvan/driver-esp32`.

### Migrating from Venus OS to driver-vedirect

`driver-vedirect` reads the SmartShunt, SmartSolar MPPT, Orion XS and Blue Smart AC charger directly over VE.Direct USB, so the Venus OS Pi can be retired. It publishes the same `battery`, `solar` and `charger` entities as `driver-venus`, but under different ids, so the swap is not invisible. A device's VE.Direct port can be read by only one thing at a time: unplug the cables from the Venus OS Pi and plug them into the hub's Pi, then leave the `venus` profile off (clear `VENUS_MQTT_HOST` in `.env`).

What changes:

- **Ids.** `driver-venus` ids are Venus device instances (for example `290`). `driver-vedirect` ids are the lowercased `SER#` the device reports. The SmartShunt sends no serial number, so its id is the tail of its `/dev/serial/by-id` path (for example `if02`).
- **Primary battery.** `renewvan/hub/primary-battery` still holds the old Venus instance id, which no longer exists. Clients fall back to `auto` (first complete battery by sorted id) and say so. Pick the shunt again in Settings once it appears, so the headline battery is the one you mean (ADR 0019).
- **Logger history.** The logger writes InfluxDB points tagged by `id`. The new ids start new series: the old history stays under the Venus ids and is not renamed or merged. The Grafana **Battery** dropdown and the per-id panels list both, so choose the new id for current data and the old one for history. The loads figure needs the new battery and solar ids chosen together.
- **Shunt port stability.** The SmartShunt's id comes from its adapter port, so it stays the same only while its cable stays in the same port of the same adapter (the van uses one Duppa quad-port adapter). Moving it to another port, or changing the adapter, gives it a new id, a new history series and a new primary-battery pick. Devices with a serial number are found by what they report and can be replugged anywhere.
- **Topics not published.** `battery` `charge_state`, `capacity_ah`, `remaining_ah` and `solar` `yield_history_wh` are absent; history comes from the database. Charger `power_w` is derived as voltage times current when the device sends none.
- **Silence.** After 10 s without a valid frame from a device its live values are cleared (empty retained payloads). Its identity and the Driver's health stay, so an unplugged device shows as missing, not frozen (ADR 0008).

### Volumes

Data persists in named Docker volumes: `mosquitto-data`, `mosquitto-log`, `influxdb-data`, `grafana-data`, `caddy-data`, `caddy-config`. Do not run `docker compose down -v` casually: `caddy-data` holds the TLS root every phone trusts.

## Grafana

Grafana is for history: charts over what the `logger` wrote to InfluxDB. It is separate from the live dashboard and is not on the kiosk. It publishes on `127.0.0.1:3000` only, so reach it from your own machine with an SSH tunnel (`ssh -L 3000:localhost:3000 <pi>`, then `http://localhost:3000`) or over Tailscale. Putting it on the LAN is an explicit override in `docker-compose.lan.yml`, not the default.

**Login.** The user is `admin` and the password is `GF_SECURITY_ADMIN_PASSWORD` in `/opt/renewvan/hub/.env`, generated by `bin/deploy.sh` (never `admin` / `admin`). Anonymous viewing is off. Read it with `ssh <pi> 'sudo grep ^GF_SECURITY_ADMIN_PASSWORD /opt/renewvan/hub/.env'`.

**After a deploy.** Open Connections → Data sources → InfluxDB and press Save & test: it should say "datasource is working". Then open Dashboards → `renewvan` → Van overview. The tank time series is empty when no tank value changed within the selected range; the gauges still show the last value.

**Datasource.** `InfluxDB` (Flux, `http://influxdb:8086`, org and bucket `renewvan`) is provisioned from `docker/grafana/provisioning/datasources/influxdb.yml`. Its token is `INFLUXDB_GRAFANA_TOKEN`, a read-only token scoped to the `renewvan` bucket, passed to the container by compose, so nothing secret is in git. Check it under Connections → Data sources → InfluxDB → Save & test. If you change the token in `.env`, re-run `bin/deploy.sh`, which restarts Grafana.

**Version.** Grafana runs the 12.4 line (supported to 2027-05-24). The 11.x line is out of support. The move to 13.x is due before May 2027; it migrates dashboards to unified storage on first start, so back up the `grafana-data` volume first. The 11 → 12 step rewrites the annotation table on first start: check SD-card headroom (`df -h /var/lib/docker`) before the first deploy of a van coming from 11.2.0.

**Dashboard.** "Van overview", the Grafana home page (folder `renewvan`, default range last 6 hours, refresh 30 s) comes from `docker/grafana/dashboards/van-overview.json`: current tank and battery gauges and solar power, a loads block (below), then time series for tank levels, battery state of charge, battery voltage, solar power, battery temperature and temperature probes. It only charts what the logger stores: `tank`, `relay`, `battery`, `solar`, `charger`, `router` and `temperature` measurements, tagged by `id`. Tilt is not logged yet, so there is no panel for it.

**Loads (power consumption).** No entity reports consumption, so the dashboard derives it from the energy balance: loads = solar power + charger power − battery net power (`battery.power_w` is positive while charging). It gives "Power balance" (solar, battery and loads over time), "Loads now", "Energy used in range", "Energy used per hour" in Wh, and a "Relay state" timeline of when each relay was on. Things to know:

- Pick the battery in the **Battery** dropdown at the top. A bank seen by two drivers (for example a Venus shunt and a BMS) appears as two ids, and only the one you choose is used, so nothing is counted twice. Choose the one that shares a bank with the solar controller.
- The loads figure is an estimate. It includes inverter and conversion losses and misses any power source the logger does not store, such as an alternator charger with no `charger` entity.
- Relay state is the latched command, not measured current ([CONTEXT.md](../CONTEXT.md), Relay). The timeline, like the tank series, is empty when no relay changed within the selected range.
- To see consumption per circuit you need a current sensor per load; the hub has no such entity yet.

The home page is set by `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` in `docker-compose.yml`. A home dashboard chosen in the Grafana UI (your profile or the organisation preferences) takes precedence over it; clear that choice to get Van overview back.

**Add a panel.** Provisioned dashboards are read-only in the UI, so changes go through the file:

1. Open the dashboard, choose Edit → Save as to make a scratch copy, and build the panel there. A query looks like `from(bucket: "renewvan") |> range(start: v.timeRangeStart, stop: v.timeRangeStop) |> filter(fn: (r) => r._measurement == "battery" and r._field == "power_w")`. The measurement is the entity, the field is the topic's last segment, and `id` is its third.
2. Copy the panel JSON (panel menu → Inspect → Panel JSON) into `panels` in `van-overview.json`, or export the whole dashboard (Share → Export → Export as JSON) and replace the file.
3. Commit it and run `bin/deploy.sh`. Grafana reloads dashboard files every 30 seconds.

To add a new dashboard, drop another JSON file in `docker/grafana/dashboards/`.

## Configuration

Copy `.env.example` to `.env`. The installer does this for you. `.env.example` documents every value in comments; this table summarizes them.

| Variable                                                | Default                              | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `INFLUXDB_ADMIN_PASSWORD`                               | _(generated)_                        | InfluxDB admin password, operator-only. `bin/deploy.sh` generates it; there is no default.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `INFLUXDB_ADMIN_TOKEN`                                  | _(generated)_                        | InfluxDB admin token, operator-only: passed to no container but `influxdb`. `bin/deploy.sh` generates it.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `INFLUXDB_RETENTION`                                    | `0` (forever)                        | History bucket retention: an InfluxDB duration (`8760h` = 1 year) or `0` = infinite. Only applies at bucket creation on a fresh install; an existing bucket keeps its retention until updated once — `bucket update` selects by id: `BID=$(docker exec renewvan-influxdb influx bucket list --name renewvan --token "$INFLUXDB_ADMIN_TOKEN" --hide-headers \| awk '{print $1}')`, then `docker exec renewvan-influxdb influx bucket update --id "$BID" --retention <duration> --token "$INFLUXDB_ADMIN_TOKEN"`. |
| `INFLUXDB_LOGGER_TOKEN`                                 | _(generated)_                        | Write-only token on the `renewvan` bucket, used by `logger`.                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `INFLUXDB_GRAFANA_TOKEN`                                | _(generated)_                        | Read-only token on the `renewvan` bucket, used by Grafana's datasource.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `INFLUXDB_DASHBOARD_TOKEN`                              | _(generated)_                        | Read-only token on the `renewvan` bucket, injected by Caddy's `/influx` proxy ([ADR 0024](adr/0024-history-reaches-browsers-through-a-caddy-influx-proxy.md)). Never sent to a browser.                                                                                                                                                                                                                                                                                                                         |
| `GF_SECURITY_ADMIN_PASSWORD`                            | _(generated)_                        | Grafana `admin` password.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `RENEWVAN_HOST` / `RENEWVAN_PORT`                       | `mosquitto` / `1883`                 | Broker address for services that read it from the environment.                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `MQTT_<CLIENT>_PASSWORD`                                | generated                            | One broker password per client (username = the client key), written by `bin/ensure-secrets.sh`. See [Broker security](#broker-security).                                                                                                                                                                                                                                                                                                                                                                        |
| `MOSQUITTO_CONFIG_PATH`                                 | `./docker/mosquitto/config`          | Mosquitto config directory. `bin/deploy.sh` points it at `/etc/renewvan/mosquitto/`.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `TANK_CONFIG_PATH`                                      | `./config/tank/config.ini`           | Tank driver config (calibration lives here). Must exist before first start.                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `GPS_CONFIG_PATH`                                       | `./config/gps/config.ini`            | GPS driver config.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `TEMPERATURE_CONFIG_PATH`, `TEMPERATURE_STATE_PATH`     | `./config/temperature/…`             | Config, and a writable directory for names and units edited in the dashboard.                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `TILT_CONFIG_PATH`, `TILT_STATE_PATH`                   | `./config/tilt/…`                    | Config (axis mapping), and a writable directory holding the install level zero.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `WATTCYCLE_CONFIG_PATH`                                 | `./config/wattcycle/config.ini`      | WattCycle config. The driver stays off until `mac` is set.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `VEDIRECT_CONFIG_PATH`                                  | `./config/vedirect/config.ini`       | VE.Direct driver config (`[serial] port_globs`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `AUTOTERM_CONFIG_PATH`                                  | `./config/autoterm/config.ini`       | Autoterm heater config.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `VENUS_MQTT_HOST`, `VENUS_MQTT_PORT`, `VENUS_PORTAL_ID` | empty / `1883` / empty               | The Venus OS device's own broker. Leave the host empty and `driver-venus` is not started.                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `ROUTER_ID`                                             | `main`                               | The `<id>` segment of `renewvan/router/<id>/…`. One router per hub.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `VITE_MQTT_WS_URL`                                      | `/mqtt`                              | How browsers reach the broker's WebSocket. `/mqtt` means the page's own host, proxied by the dashboard. Needs dashboard v0.7.1 or later.                                                                                                                                                                                                                                                                                                                                                                        |
| `MQTT_COMMAND_PASSWORD`                                 | generated                            | The dashboard's `command` account; compose passes it to the dashboard as `VITE_MQTT_*`. Public via `/env.js`.                                                                                                                                                                                                                                                                                                                                                                                                   |
| `DASHBOARD_BASEMAP_PATH`                                | `./config/dashboard/basemap.pmtiles` | The offline map file. `bin/deploy.sh` uses `/etc/renewvan/dashboard/basemap.pmtiles`.                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `DASHBOARD_HOST`                                        | empty, **required**                  | The Pi's LAN address or name. Compose requires it for every service; deploy refuses to run while it is empty.                                                                                                                                                                                                                                                                                                                                                                                                   |
| `TAILSCALE_BIND_ADDR`                                   | empty, Pi only                       | The Pi's Tailscale IPv4 address (`tailscale ip -4`). Caddy's 443 is published on it and on `MQTT_BIND_ADDR`, never on every interface. `bin/deploy.sh` seeds it and refuses to deploy without it.                                                                                                                                                                                                                                                                                                               |
| `MQTT_BIND_ADDR`                                        | empty, Pi only                       | The Pi's van-LAN IPv4 address. The broker (1883, 9001) is published on it and on loopback only, never on every interface. Local dev leaves it unset: loopback only. `bin/deploy.sh` seeds it from `DASHBOARD_HOST` when that is an IP, and refuses to deploy if it is not an address of the Pi. See [Broker reachability](#broker-reachability).                                                                                                                                                                |

## Deploying to a Pi step by step

`bin/install.sh` calls these scripts. Run them one at a time if you want control over one step. `--host` defaults to the `renewvan` SSH alias, so set that up in `~/.ssh/config` first.

1. **One-time:** `bin/bootstrap-pi.sh [--host <ssh-alias>]`. Installs Docker Engine and the Compose plugin if missing. Creates `/opt/renewvan/hub` (deployed compose artifacts), `/etc/renewvan` (host config: Mosquitto, driver configs), and `/var/log/renewvan`.
2. **Repeatable:** `bin/deploy.sh [--host <ssh-alias>]`. Rsyncs `docker-compose.yml` and `docker/` to `/opt/renewvan/hub`, seeds each driver's `/etc/renewvan/<driver>/config.ini` from `docker/<driver>/` if missing, then runs `docker compose pull && up -d`. On a fresh Pi it seeds `.env` from `.env.example` (you still set `DASHBOARD_HOST`, and `VENUS_*` if the van has Venus OS). Before `pull && up -d` it runs `bin/ensure-secrets.sh`, which generates every missing secret and the three scoped InfluxDB tokens into `.env` (mode 600). It never overwrites an existing `.env`; see [Secrets and rotation](#secrets-and-rotation). It stops if `renewvan-kiosk` does not exist yet (run `bin/bootstrap-pi.sh` or `bin/bootstrap-kiosk-pi.sh`).
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

- `caddy`: host port 443 on the van-LAN and Tailscale addresses only (see [Broker reachability](#broker-reachability)).
- `dashboard`: `127.0.0.1:80` only, for the kiosk browser (`http://localhost`).

### Setup

1. Keep a `renewvan/dashboard` checkout next to this repo (`../dashboard`). `bin/deploy.sh` ships its `docker/Caddyfile` to the Pi, and compose mounts it from there.
2. On the Pi, set `DASHBOARD_HOST` in `/opt/renewvan/hub/.env` to its LAN address (`hostname -I | awk '{print $1}'`).
3. Run `bin/deploy.sh`. It restarts Caddy, because the Caddyfile is a single-file bind mount that a redeploy does not reload.

### The login

Caddy puts one shared `basic_auth` login in front of the whole site, so `/mqtt` (which can switch relays) and `/influx` are never open to whoever reaches 443 ([ADR 0025](adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md)). The username is `renewvan`. `bin/ensure-secrets.sh` generates the password on the first deploy and prints it once; only its bcrypt hash is kept (`CADDY_BASIC_AUTH_HASH` in `.env`). Lost it? `bin/rotate-secret.sh caddy` prints a new one. The browser asks once per device and remembers it. The kiosk is not behind the login: it loads `http://localhost` (the dashboard container on the Pi's loopback), never this site. Check from a laptop with `CHECK_EXPOSURE_PASSWORD=<password> bin/check-exposure.sh <pi-lan-ip>`.

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

The root is valid for 10 years. Caddy signs the server certificate directly with it (`sign_with_root`) for 90 days and renews it automatically, so renewals need no reinstall. Never delete the `caddy-data` volume: every device would have to trust a new root. The volume also holds the root's private key, so treat it (and any backup of it) as a secret: anyone with it can issue certificates your phones trust.

## Broker reachability

Until the cutover the broker is anonymous, and after it the broker is still private, so it must be reachable from the van's own network and from nowhere else. `docker-compose.yml` publishes ports 1883 and 9001 on loopback only (local dev, host plugins: kiosk, tailscale). On the Pi, `docker-compose.lan.yml` adds `MQTT_BIND_ADDR`, its van-LAN address, never on `0.0.0.0`. Containers reach the broker over the compose network, and browsers use the dashboard's `/mqtt` proxy, so neither needs the host ports. This is a hard precondition for enabling heater commands (see [ADR 0021](adr/0021-writable-heater-and-its-command-safety-rules.md)).

Caddy's 443 (dashboard, `/mqtt`, `/influx`) follows the same rule: `docker-compose.yml` publishes it on loopback, and the Pi override adds `MQTT_BIND_ADDR` and `TAILSCALE_BIND_ADDR` (the Pi's `tailscale ip -4`), never every interface ([ADR 0025](adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md)). `bin/deploy.sh` seeds `TAILSCALE_BIND_ADDR` and stops if tailscale has no address (run `sudo tailscale up` first). If tailscale is not up at boot, Caddy cannot bind and stays down until it is: restart it with `docker compose up -d caddy`. Run `bin/check-exposure.sh --published` on the Pi to confirm no all-interface publish.

`bin/deploy.sh` selects the override by setting `COMPOSE_FILE=docker-compose.yml:docker-compose.lan.yml` in the Pi's `.env`, so plain `docker compose` commands on the Pi use it too. A local checkout needs no setup.

If the Pi's IP changes (new router), `bin/deploy.sh` stops with an error naming `MQTT_BIND_ADDR`; set it to the new address and re-run.

### Verify

1. On the Pi, check the listeners: `ss -ltn | grep -E ':(1883|9001)\b'` shows only `127.0.0.1` and the van-LAN address, never `0.0.0.0` or `*`.
2. From a van-LAN device (laptop on the van Wi-Fi): `mosquitto_sub -h <MQTT_BIND_ADDR> -t 'renewvan/#' -C 1 -W 5` receives a message, and the dashboard shows live data.
3. From a device on the uplink side (a phone on cellular, not on the van Wi-Fi, with the Pi's uplink address from `ip -4 addr` on the uplink interface): `nc -zv -w 5 <uplink-address> 1883` and the same for `9001` must fail (refused or timed out).
4. `docker compose ps` shows every driver `Up`, and Settings > Drivers lists them as healthy.

Authentication and ACLs are out of scope here; see [Broker security](#broker-security).

## Secrets and rotation

`.env` on the Pi (`/opt/renewvan/hub/.env`, mode 600, enforced on every deploy, readable by the deploy user only) holds the InfluxDB admin password and token, the three scoped tokens and the Grafana admin password. Compose has no fallback for any of them, so a plain `docker compose up` without them fails instead of starting on a public value. `.env.example` holds empty placeholders only.

`bin/deploy.sh` runs `bin/ensure-secrets.sh` on every deploy. It is idempotent and never wipes a volume:

- It fills in anything empty or still a placeholder or legacy default (`renewvan-dev-token`, `renewvan-dev-only`, `admin`), and creates the missing `INFLUXDB_LOGGER_TOKEN` (write-only), `INFLUXDB_GRAFANA_TOKEN` and `INFLUXDB_DASHBOARD_TOKEN` (read-only) on the `renewvan` bucket.
- **Existing vans.** InfluxDB and Grafana only read their initial credentials on a volume's first run, so for a van that already has data volumes the script rotates in place: it creates a new admin token through the old one, deletes the old one, changes the admin password with `influx user password`, and runs `grafana cli admin reset-admin-password`. A van whose admin values were already non-default only gets the three tokens and the Grafana password. It prints the variable names it changed, never the values.
- **Manual rotation of one value.** Delete its line from `.env` and run `bin/deploy.sh`. A scoped token is revoked in InfluxDB with `docker exec -e INFLUX_TOKEN=<admin token> renewvan-influxdb influx auth list` then `influx auth delete --id <id>`.

**Broker accounts** ([ADR 0025](adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md)). The same step generates one `MQTT_<CLIENT>_PASSWORD` per client (the username is the client key), writes the hashed password file and the ACL to `/etc/renewvan/mosquitto/` (`passwd`, `acl`, mode 640, group `1883` so the broker can read them), puts each driver's and plugin's credential where it reads it (`[mqtt]` in `/etc/renewvan/<driver>/config.ini` for tank, gps, temperature, tilt and wattcycle; `/etc/renewvan/{kiosk,tailscale,hub}.env`), and creates the shared Caddy login (`CADDY_BASIC_AUTH_USER`/`CADDY_BASIC_AUTH_HASH`; the password is printed once and never stored). Clients whose file changed are listed in `.env.changed-clients`. `BROKER_ACL_OPEN=1` is the migration state: the ACL keeps a temporary `pattern readwrite renewvan/#` line and `allow_anonymous` is `true`. `bin/cutover-broker-acl.sh` sets it to `0` (no such line, `allow_anonymous false`, [ADR 0026](adr/0026-anonymous-clients-are-refused-after-the-cutover.md)) and it stays closed. Every account reads `renewvan/#` through a `pattern read` line, not `topic read`: a `topic` line before the first `user` applies to anonymous clients only, so authenticated drivers would read nothing. `reader` is the read-only login for MQTT Explorer and dev tools; it writes nothing. The ACL deviates from the ADR's one-line `pattern write renewvan/%u/#`: a driver's entity topics are `renewvan/<entity>/…`, so each account lists the namespaces it really writes (`bin/broker-accounts.sh`), and autoterm, vedirect and the hub plugin are accounts too. A leaked `autoterm` credential can write `renewvan/heater/+/set`.

Rotate one client with `bin/rotate-secret.sh <client>` (or `caddy` for the dashboard login): it replaces that password in `.env`, the password file and the client's own file, sends the broker SIGHUP and restarts the client. The ESP32 is refused: rotate it by OTA with a new `secrets.yaml`, only on suspected compromise.

The admin token is for operators (`docker exec`, the `influx` CLI). No container but `influxdb` receives it.

## Verifying security hardening

Everything here is also covered by CI where it can be (`bin/test_compose_hardening.py`, `bin/test_pinning.py`, `bin/test_ensure_secrets.py`); the Pi checks are run by hand after any deploy that touched compose, the bootstrap scripts or plugin permissions. Nothing is scheduled.

| Check                                                  | Where it runs                | What it proves                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------ | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bin/check-exposure.sh <pi-lan-ip>`                    | A second host on the van LAN | 8086 and 3000 refuse connections on the LAN address; `/influx` and `/mqtt` answer 401 without the Caddy login; with `CHECK_EXPOSURE_PASSWORD` set, a query through `/influx` answers and a write and other InfluxDB paths are rejected. `--published` (on the Pi) fails if Caddy's 443 is published on every interface. Needs `nc` and `curl`. Exit 0 = pass.                                                                                     |
| `bin/check-hardening.sh`                               | On the Pi, by hand           | `renewvan-kiosk` is not in `sudo` or `docker` and `sudo -n true` fails for it; Chromium and `kiosk.py` run as it; `/opt/renewvan/plugins` and `/opt/renewvan/venvs` are root-owned and not group or world writable; the wattcycle and vedirect containers run with a non-zero uid, `cap_drop: ALL` and `no-new-privileges`; the D-Bus policy file is installed; vedirect's `/dev` is read-only; each driver still reaches D-Bus or a serial port. |
| `bin/test_compose_hardening.py`, `bin/test_pinning.py` | CI (`pytest bin/`)           | Static: loopback publishes, no secret defaults, scoped tokens per consumer, non-root drivers, `/dev:ro`, D-Bus policy uid matches compose; `tag@sha256` for infra images, exact tags for `renewvan/*`, SHA-pinned Actions, exact `npx` versions, no `curl \| sh`.                                                                                                                                                                                 |

Manual spot checks: on the Pi, `ss -ltn | grep -E ':(8086|3000)\b'` shows only `127.0.0.1`; `docker exec renewvan-wattcycle id` and `docker exec renewvan-vedirect id` show a non-root uid.

## Kiosk user and plugin ownership

The kiosk browser (`renewvan-cage.service`) and the display bridge (`renewvan-kiosk.service`) run as `renewvan-kiosk`: a system user with a locked password and no login shell, in the `video`, `input` and `render` groups only. It has no `sudo` and is not in `docker`. The browser therefore runs with only the access it needs. `bin/ensure-kiosk-user.sh` creates it (called by `bin/bootstrap-pi.sh` and `bin/bootstrap-kiosk-pi.sh`).

`/opt/renewvan/plugins` and `/opt/renewvan/venvs` are `root:root` 755: they hold code systemd runs, some of it as root. `bin/deploy.sh` writes them with `sudo rsync` and re-owns them to root. `/opt/renewvan/hub` (compose, `.env`) and `/etc/renewvan` stay owned by the admin user, which is in the `docker` group. The `hub` and `tailscale` plugins keep running as the admin user (`$SUDO_USER`), now reading root-owned code.

**Migrating a Pi that ran the kiosk as the admin user.** Re-running the bootstrap is the migration; `bin/deploy.sh` never migrates, it stops with "run bin/bootstrap-kiosk-pi.sh" while `renewvan-kiosk` is missing:

1. `bin/bootstrap-kiosk-pi.sh --host <pi>`: creates the user, reinstalls both units with it and restarts the session.
2. `bin/bootstrap-pi.sh --host <pi>`: re-chowns `plugins` and `venvs` to root, installs the wattcycle D-Bus policy.
3. `bin/deploy.sh --host <pi>`, then `sudo reboot` (or restart `renewvan-cage`).
4. Run `bin/check-hardening.sh` on the Pi. The old Chromium profile under the admin home is abandoned; the kiosk keeps no state.

## Driver hardening

`driver-wattcycle` runs as the fixed uid 10001 with `cap_drop: [ALL]` and `no-new-privileges`. It keeps the host's `/var/run/dbus` mount for BlueZ, so the host carries a D-Bus policy (`/etc/dbus-1/system.d/renewvan-wattcycle.conf`, written by `bin/bootstrap-pi.sh`) that lets that uid talk to `org.bluez` and denies `org.freedesktop.systemd1`. `driver-vedirect` runs as uid 10002 with group `dialout` (gid 20), the same capability and privilege settings, and `/dev:/dev:ro`, which still allows serial I/O but not creating device nodes; the `c 188:*` and `c 166:*` cgroup rules stay. If a driver image cannot run under its uid (file ownership of `/app`, config read), file a ticket in that driver's repo and keep the previous pinned tag until the release lands.

## Broker security

The repo's Mosquitto config runs `allow_anonymous true`, which is right for local dev and for the migration. On the Pi, `bin/validate-broker-config.sh` appends the real value from `BROKER_ACL_OPEN`: `true` while migrating, `false` after the cutover. Every client has its own account ([ADR 0025](adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md)); `bin/ensure-secrets.sh` generates the passwords and puts each where that client reads it, and `bin/deploy.sh` restarts the clients whose credential file changed:

| Client                                                                                         | Where it reads its credential                                                    |
| ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `logger`, `driver-venus`, `driver-router`, `driver-demo`, `driver-autoterm`, `driver-vedirect` | `MQTT_USERNAME`/`MQTT_PASSWORD`, filled by compose from `MQTT_<CLIENT>_PASSWORD` |
| `driver-tank`, `driver-gps`, `driver-temperature`, `driver-tilt`, `driver-wattcycle`           | `[mqtt]` section of `/etc/renewvan/<driver>/config.ini`                          |
| Host plugins (kiosk, tailscale, hub)                                                           | `/etc/renewvan/kiosk.env`, `tailscale.env`, `hub.env`                            |
| ESP32 relay                                                                                    | its `secrets.yaml`                                                               |
| Browsers                                                                                       | `VITE_MQTT_*`, the `command` account, behind Caddy's login                       |

The repo config also sets `max_connections 100`, `message_size_limit 1048576` and logs to stdout only (Docker rotates it). On the Pi, `bin/validate-broker-config.sh` (run by `bin/deploy.sh`) rebuilds `/etc/renewvan/mosquitto/mosquitto.conf` on every deploy from the repo file plus `docker/mosquitto/auth.conf` (`password_file` and `acl_file`, global, so both listeners load them): hand edits to the installed copy are replaced. Before the running broker is touched it starts a throwaway broker (no network) on the candidate config and the generated `passwd`/`acl`, because `mosquitto --test-config` does not read the ACL and a bad ACL line makes the broker exit on reload. If that broker does not stay up, the deploy stops, the previous `passwd`/`acl` are restored and the running broker is untouched. When the config changed deploy restarts `mosquitto`, otherwise it sends SIGHUP.

Give every client its credential first, then require authentication, and keep the old `mosquitto.conf` to roll back to. Locking the broker with only the environment pair set takes the five config-driven drivers offline (`MQTT connect failed with rc=5`). The design is [ADR 0025](adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md).

### Migrating to per-client accounts

The order matters ([ADR 0025](adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md)); every step has a rollback and none needs a reflash while the old firmware still works.

1. **Bind 443** to the LAN and Tailscale addresses (`bin/deploy.sh` does it; check with `bin/check-exposure.sh --published` on the Pi).
2. **Deploy.** `bin/deploy.sh` generates the passwords, writes the password file and an ACL that still contains the temporary `pattern readwrite renewvan/#` line (`BROKER_ACL_OPEN=1`, `allow_anonymous true`), validates them in a throwaway broker, and reloads. Drivers, plugins and the dashboard restart on their own accounts; anonymous clients still work.
3. **Flash the ESP32** while its old anonymous firmware still works, so the 15-minute authentication-failure reboot never fires. On the Pi: `grep MQTT_ESP32_PASSWORD /opt/renewvan/hub/.env`. In `driver-esp32/secrets.yaml` set `mqtt_username: "esp32"` and `mqtt_password` to that value, then build and OTA it (skill `renewvan-flash-esp32`). Its account may write only its own `renewvan/esp32/#`, `renewvan/relay/+/state`, `name` and `driver`, and read everything, so a leaked ESP32 password cannot switch a relay. Rotate it only by OTA, on suspected compromise.
4. **Check, then cut over.** On the Pi, `bin/check-broker-auth.sh --stage3` must report no anonymous client (a client refused with `rc=5` needs a restart; exclude harmless readers with `CHECK_ALLOW_ANON=<regex on client id>`). Anything that publishes must have an account by then: the RUT360 router (Data to Server MQTT: username `router`, password `MQTT_ROUTER_PASSWORD`), MQTT Explorer (use `reader`, password `MQTT_READER_PASSWORD`) and every open browser tab (reload it). Then `bin/cutover-broker-acl.sh` removes the temporary line, sets `allow_anonymous false`, validates, reloads (SIGHUP, no restart) and re-checks; a failed check puts the migration state back. Roll back by hand with `bin/cutover-broker-acl.sh --rollback`.

`bin/check-broker-auth.sh` proves each rule by effect, because a denied publish is silent on MQTT 3.1.1: it publishes as one account and watches a subscriber logged in as `reader`; after the cutover it also checks that an anonymous client cannot connect. It uses an unknown relay id and unretained payloads, so nothing real is switched.

## Host plugins

Plugins under `plugins/` are Pi-level systemd services that run outside Docker.

| Plugin              | What it does                                                                                             | Docs                                                                            |
| ------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `plugins/hub`       | Publishes the Pi's LAN address and the hub version; holds the primary-battery and entity-icons settings. | [README](../plugins/hub/README.md)                                              |
| `plugins/kiosk`     | Display power, brightness, and idle auto-sleep for the touchscreen.                                      | [README](../plugins/kiosk/README.md), [sleep/wake](kiosk-display-sleep-wake.md) |
| `plugins/tailscale` | Installs Tailscale and publishes VPN status. Active after `sudo tailscale up`.                           | [README](../plugins/tailscale/README.md)                                        |

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

To add a driver, see [authoring-a-driver.md](authoring-a-driver.md).

## Plain Docker Compose

`docker-compose.yml` is the plain-Compose-host path with named volumes, for any Linux machine running Docker:

```bash
cp .env.example .env     # fill in DASHBOARD_HOST, and generate every empty secret yourself (openssl rand -hex 32);
                         # compose refuses to start while one is empty. Create the three scoped InfluxDB tokens
                         # as in ensure-secrets.sh, or run InfluxDB-less by not using the logger/grafana services.
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
