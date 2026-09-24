# Architecture

## Terminology

A few terms get used a lot below and across the split-out driver repos, so worth pinning down once:

- **Driver** — a small process (or firmware component) that talks to one specific device and normalizes its data into the shared schema. This is the functional term used throughout this document, and matches the vocabulary the ecosystems we bridge already use (Venus OS's dbus-serialbattery, Home Assistant's integrations, ESPHome's components).
- **Node** — a physical piece of edge hardware (an ESP32 board, a Pi, a wall panel). A node may run its own driver *internally* — e.g. ESPHome firmware that publishes directly in our schema, no separate process needed — or it may *require* a driver running elsewhere (typically on the Pi) when the hardware can't speak MQTT/the schema on its own, such as [VE.Direct](http://VE.Direct) serial, a BLE BMS, or RV-C over CAN. Don't assume every node needs a Pi-side driver; check whether it's self-describing first.
- **Plugin** — the broader installable-extension concept: drivers are one kind of plugin, but so are UI panels and (eventually) automation rule-packs. This is the term used specifically for the registry described under **Delivery and distribution** — not a synonym for "driver."
- **Package** — the distribution mechanism only (how a driver or plugin ships — e.g. as its own PyPI or npm package). Says nothing about what the thing does; use driver/plugin/node for that.

## Design principles

**Bus-centric and decoupled.** Every device — a Victron MPPT, a JK BMS over BLE, an RV-C dimmer, an ESP32 relay — publishes to one MQTT broker under a shared topic and schema convention. Every consumer (UI, automation, logging) reads from and writes to that bus rather than talking to drivers directly. This is what makes the platform composable with Venus OS, Home Assistant, Signal K, and RV-C instead of competing with them.

**Compute at the edges, intelligence at the center, safety in hardware.** Cheap ESP32 nodes own time-critical, safety-adjacent local behavior — pump dry-run cutoffs, dimmer PWM, thermostat hysteresis — and degrade gracefully if the Pi or network goes down. The Pi owns state, history, rules, and UI. Hard safety (over-current, gas shutoff, charge-disconnect) lives in dedicated hardware — fuses, BMS, thermal cutoffs — never solely in software.

**Plugin-first.** A driver is a small process speaking the bus contract. Each device type gets its own driver, cleanly separated from the publisher logic, so adding hardware support means writing a driver, not touching the core.

## Layered architecture


| Layer                     | Components                                                                                                                     | Recommended tech                                                                          |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| **Physical devices**      | Batteries/BMS, inverter-charger, MPPT, pumps, heaters, lights, tanks, doors, gas/CO/smoke, vehicle CAN                         | —                                                                                         |
| **Distributed nodes**     | Relay/dimmer nodes, tank nodes, shunt node, wall panels                                                                        | ESP32 + ESPHome/Tasmota; INA226/INA3221 for shunts; Nextion or LVGL panel                 |
| **Edge adapters/drivers** | [VE.Direct/VE.Can/VE.Bus](http://VE.Direct/VE.Can/VE.Bus), serial-BMS, BLE sensors, RV-C CAN bridge, Modbus/RS-485, 1-Wire/I2C | Python driver processes; MCP2515 CAN HAT or USB-CAN for RV-C; ADS1115 for analog senders  |
| **Message bus**           | Single broker, retained state, command topics, discovery                                                                       | Mosquitto; topic scheme `van/<domain>/<device>/<property>`; Home Assistant MQTT discovery |
| **Application services**  | Device registry, automation engine, alarms, logging, config, OTA                                                               | Docker Compose on Pi 4/5; InfluxDB + Grafana for history                                  |
| **Presentation**          | Kiosk touchscreen, phone/tablet PWA, ESP32 panels, Grafana                                                                     | Web UI over WebSocket/MQTT-over-WS, offline-first                                         |


## The unified device model

The single most valuable artifact this project produces isn't code — it's the versioned JSON schema in `/schema` that every driver normalizes into and every consumer reads from. v0 formalizes three of these entities as JSON Schema files (`tank.schema.json`, `relay.schema.json`, `battery.schema.json`, plus validating example fixtures under `schema/examples/`); the rest of this list is the longer-term v1+ shape the same schema directory will grow into:

- **battery** — voltage, current, SoC, charge/discharge limits, cell data
- **tank** — level %, liters, calibration curve, type (fresh/grey/black/LPG)
- **shunt** — coulomb-counted SoC, optional fusion with BMS-reported SoC
- **heater** — fuel type, safety state
- **pump**, **light** (on/off/PWM), **climate**
- **safety** — gas/CO/smoke, with latching alarm semantics
- **vehicle** — ignition, motion, chassis battery

Every device also declares **capability and failure semantics**: every command has an acknowledgement topic and a timeout, every device has a `health` topic with heartbeat expectations, and automation rules declare preconditions (e.g., a heater start requires gas-OK + voltage-OK + stationary). Publishing this schema separately from the code lets hardware makers and driver authors conform without reading the implementation.

Practical priorities: tank calibration as a first-class UX (top/bottom, multi-point), a shunt SoC engine with coulomb counting plus optional BMS/shunt fusion, and motion/ignition-inhibit rules for anything unsafe while driving.

## Hardware reference build

The official reference build stays boring and purchasable, not custom hardware:

- Raspberry Pi 4/5 (2–4 GB), booting from SSD or high-endurance SD
- 12 V→5 V buck converter with proper fusing
- Optional UPS/HAT with clean-shutdown signaling
- CAN interface (MCP2515 HAT or USB-CAN) for RV-C/VE.Can
- USB-RS485 for Modbus devices
- 5–7" touchscreen running a kiosk browser

Everything beyond that — relays, dimmers, tank senders — is documented as a **recipe** ("ESP32 + 8-channel relay board", "ESP32-C3 + INA226 + 75 mV shunt") rather than a custom PCB.

Two hardware rules matter more than the rest: **never put mains or high-current switching on the Pi carrier** — that belongs in dedicated relays/contactors or certified devices, with the hub only sending commands — and **offer an always-on low-power tier**, an ESP32 watchdog node that can keep pumps/heat-protection running and reboot the Pi if it hangs.

## Integration strategy: bridge, don't fight

- **Venus OS** — consume Venus's MQTT feed for full device coverage on a Victron-equipped van, and package normalized devices back into Venus OS using established dbus-mqtt driver patterns
- **Home Assistant** — emit MQTT discovery messages so every van entity appears automatically
- **Signal K** — export the overlapping schema (tanks, batteries, environment) for vanlifers who are also sailors
- **RV-C** — an in/out CAN bridge for factory multiplex systems

## Delivery and distribution

- A flashable Raspberry Pi OS image, plus Docker Compose for the already-have-a-Pi crowd
- An npm/PyPI-style plugin registry so drivers and UI panels are one-command installs
- A hardware simulator (fake BMS, fake tank, fake CAN traffic on a virtual CAN interface) so contributors can build drivers without a van
- Remote access via Tailscale rather than custom cloud infrastructure in v1

