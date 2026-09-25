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

bridges *into* the ecosystems that already exist — Venus OS, Home

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
in their own repo under the `renewvan` GitHub org (`tank`, `battery`,
`logger`) — see `.scratch/renewvan-hub-v0-build/` for the v0 build spec.
Not yet ready for production van use — see \[Roadmap\](#roadmap).

## Install

1. **Infra already running.** `docker-compose-truenas.yml` (Mosquitto,
   InfluxDB, Grafana) is deployed separately and stays up independent of
   the backend services below — see the file's header for the TrueNAS
   Custom-App YAML-editor flow, or `docker compose -f
   docker-compose-truenas.yml up -d` for a plain Compose host.
2. **Copy `.env.example` to `.env`** and fill in the required values
   (Victron GX device reachability, the dashboard's browser-reachable
   MQTT-WS URL) — see `.env.example`'s comments for what each value is
   and where it's used.
3. **Pull the pinned backend images and start them:**
   `docker compose -f docker-compose-truenas.yml pull && docker compose
   -f docker-compose-truenas.yml up -d`. This brings up `tank`,
   `battery`, `logger`, and `dashboard` alongside the infra layer — each
   a pinned, independently-published image (own repo, own release;
   nothing built from source in `hub`, per
   `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`).
   Bumping a service later is a single `*_IMAGE_TAG` edit in `.env`.
4. **Flash the ESP32 relay node**, once, per `renewvan/relay`'s own
   README (ESPHome YAML, no custom firmware).
5. **Install the phone app** per `renewvan/mobile`'s own README (React
   Native — sideload or app-store build, not part of this compose flow).

## Architecture

See \[`/docs/[architecture.md](http://architecture.md)`\](docs/[architecture.md](http://architecture.md)) for the full layered

design (edge nodes → drivers → MQTT bus → application services →

presentation).