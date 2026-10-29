# renewvan/hub

Open-source, open-hardware campervan control hub for Raspberry Pi, built on MQTT.

## What is this?

Monitoring a campervan's batteries, tanks, and appliances usually means picking a side. Victron's Venus OS is mature but centered on Victron hardware. Home Assistant is fully open but has no van-specific packaging: no tank calibration workflow, no shunt-based state of charge, no gas-safety interlocks.

renewvan/hub takes a different approach. An MQTT broker is the single source of truth, a shared device model describes van entities (batteries, tanks, heaters, pumps, safety sensors), and bridges connect it to the ecosystems that already exist: Venus OS, Home Assistant, Signal K, and RV-C. Any driver, whether it talks to a Victron MPPT or an ESP32 relay board, normalizes into the same schema and appears on the same bus.

The goal is not to replace those platforms. It is to be the open, vendor-agnostic glue between them.

## Status

Early, hobby-scale (v0). Not yet ready for production van use.

This repo holds the shared device-model schema, the Docker Compose deployment, the host-level Pi plugins, and the docs. Drivers, the logger, the dashboard, and the phone app each live in their own repo under the [`renewvan`](https://github.com/renewvan) organization and ship as pinned container images.

## Install

You need a Raspberry Pi running Raspberry Pi OS (Debian 13, 64-bit) that you can `ssh` into with a key. From your own machine:

```bash
git clone https://github.com/renewvan/hub && cd hub
bin/install.sh --host <ssh-alias-or-user@pi>
```

That is the whole install. It checks the Pi, installs Docker, generates credentials, starts the stack, and sets up the touchscreen. It is safe to re-run and never overwrites an existing `.env`. Reboot the Pi once afterwards.

Optional flags: `--venus-host`, `--venus-portal-id`, `--public-host`, `--no-kiosk`, `--no-rotate`, `--yes`. All of them, and the manual steps that remain (Tailscale login, tank calibration, broker authentication), are in the [reference](docs/reference.md#install).

Broker authentication is **off** by default. Read [Broker security](docs/reference.md#broker-security) before exposing the Pi beyond your own network.

## Try it without a van

Switch on Settings → General → System → Demo mode in any dashboard. A simulated van replays on the bus without touching real data. See [Demo mode](docs/demo-mode.md).

## Documentation

| Document                                                           | What it covers                                                       |
| ------------------------------------------------------------------ | -------------------------------------------------------------------- |
| [Reference](docs/reference.md)                                     | Install, services, configuration, deployment, HTTPS, troubleshooting |
| [Architecture](docs/architecture.md)                               | Layered design, principles, and the unified device model             |
| [Authoring a driver](docs/authoring-a-driver.md)                   | Writing a driver that conforms to the schema                         |
| [Porting a Venus dbus driver](docs/porting-dbus-to-mqtt-driver.md) | Moving an existing driver onto MQTT                                  |
| [Agent skills](docs/agent-skills.md)                               | Claude Code skills that automate drivers, maps, deploys              |
| [Demo mode](docs/demo-mode.md)                                     | The simulated van                                                    |
| [Kiosk display sleep and wake](docs/kiosk-display-sleep-wake.md)   | Touchscreen power behavior                                           |
| [Domain vocabulary](CONTEXT.md)                                    | Terms used across the project                                        |
| [Decisions](docs/adr)                                              | Architecture decision records                                        |

## Repo layout

| Path                 | Contents                                                              |
| -------------------- | --------------------------------------------------------------------- |
| `docker-compose.yml` | The stack. Bump a service by editing its `image:` tag.                |
| `schema/`            | JSON Schemas, example fixtures, and demo scenarios                    |
| `bin/`               | `install.sh`, `bootstrap-pi.sh`, `deploy.sh`, `bootstrap-kiosk-pi.sh` |
| `plugins/`           | Pi-level systemd services: `hub`, `kiosk`, `tailscale`                |
| `scripts/`           | Helpers, such as `push-basemap.sh` for the offline map                |
| `docs/`              | Documentation and decision records                                    |

## Contributing

After any schema change, run both validators:

```bash
./schema/validate-examples.sh
python3 schema/validate-demo.py   # needs: pip install jsonschema
```

Formatting and linting run in a pre-commit hook. See [ADR 0007](docs/adr/0007-per-language-formatting-linting-convention.md).

## License

See [LICENSE](LICENSE).
