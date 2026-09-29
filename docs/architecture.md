# Architecture

## Terminology

A few terms get used a lot below and across the split-out node repos, so worth pinning down once:

- **Node** — anything that publishes one specific device or feed onto the renewvan bus, normalized into the device model. This covers both ESPHome firmware on an ESP32 host (the relay node) and a Python process on the Pi (the tank and battery nodes). The term matches ESPHome nodes and Node-RED nodes. _Avoid: Driver (superseded)._
- **Host** — the physical hardware a node runs on: the Raspberry Pi, an ESP32 board, a wall panel. One host may run several nodes; a node is never a host. _Avoid: Node (for the hardware), board, device._
- **Plugin** — the broader installable-extension concept: nodes are one kind of plugin, but so are UI panels and (eventually) automation rule-packs. This is the term used specifically for the registry described under **Delivery and distribution** — not a synonym for "node."
- **Package** — the distribution mechanism only (how a node or plugin ships — e.g. as its own PyPI or npm package). Says nothing about what the thing does; use node/plugin/host for that.
- **Renewvan hub** — the Docker Compose stack running on the Pi: the Mosquitto broker (renewvan bus), pinned node containers, and plugins. The user-facing name for the Pi-side deployment; "Renewvan hub connected" in any UI means an active MQTT-over-WebSocket connection to this stack. _Avoid: "Bus connected" (ambiguous — could mean vehicle CAN bus or MQTT protocol)._

## Design principles

**Bus-centric and decoupled.** Every device — a Victron MPPT, a JK BMS over BLE, an RV-C dimmer, an ESP32 relay — publishes to one MQTT broker under a shared topic and schema convention. Every consumer (UI, automation, logging) reads from and writes to that bus rather than talking to nodes directly. This is what makes the platform composable with Venus OS, Home Assistant, Signal K, and RV-C instead of competing with them.

**Compute at the edges, intelligence at the center, safety in hardware.** Cheap ESP32 nodes own time-critical, safety-adjacent local behavior — pump dry-run cutoffs, dimmer PWM, thermostat hysteresis — and degrade gracefully if the Pi or network goes down. The Pi owns state, history, rules, and UI. Hard safety (over-current, gas shutoff, charge-disconnect) lives in dedicated hardware — fuses, BMS, thermal cutoffs — never solely in software.

**Plugin-first.** A node speaks the bus contract. Each device type gets its own node, cleanly separated from the publisher logic, so adding hardware support means writing a node, not touching the core.

## Layered architecture


| Layer                     | Components                                                                                                                     | Recommended tech                                                                          |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| **Physical devices**      | Batteries/BMS, inverter-charger, MPPT, pumps, heaters, lights, tanks, doors, gas/CO/smoke, vehicle CAN                         | —                                                                                         |
| **Hosts**                 | Pi (runs Pi-side nodes), ESP32 boards (run ESPHome firmware nodes), wall panels, CAN HAT, ADS1115 breakout | Raspberry Pi 4/5; ESP32/ESP32-C3; MCP2515 HAT or USB-CAN for RV-C; INA226/INA3221 for shunts |
| **Nodes**                 | ESPHome relay firmware node, tank node (ADS1115 → Pi process), battery node (Victron remap → Pi process), [VE.Direct/VE.Can/VE.Bus](http://VE.Direct/VE.Can/VE.Bus), serial-BMS, BLE sensors, RV-C bridge, Modbus/RS-485, 1-Wire/I2C | ESPHome YAML (firmware); Python node processes (Pi); ADS1115 for analog senders |
| **Message bus**           | Single broker, retained state, command topics, discovery                                                                       | Mosquitto; topic scheme `renewvan/<domain>/<device>/<property>`; Home Assistant MQTT discovery |
| **Application services**  | Device registry, automation engine, alarms, logging, config, OTA                                                               | Docker Compose on Pi 4/5; InfluxDB + Grafana for history                                  |
| **Presentation**          | Kiosk touchscreen, phone/tablet PWA, ESP32 panels, Grafana                                                                     | Web UI over WebSocket/MQTT-over-WS, offline-first                                         |


## The unified device model

The single most valuable artifact this project produces isn't code — it's the versioned JSON schema in `/schema` that every node normalizes into and every consumer reads from. v0 formalizes three of these entities as JSON Schema files (`tank.schema.json`, `relay.schema.json`, `battery.schema.json`, plus validating example fixtures under `schema/examples/`); the rest of this list is the longer-term v1+ shape the same schema directory will grow into:

- **battery** — voltage, current, SoC, charge/discharge limits, cell data
- **tank** — level %, liters, calibration curve, type (fresh/grey/black/LPG)
- **shunt** — coulomb-counted SoC, optional fusion with BMS-reported SoC
- **heater** — fuel type, safety state
- **pump**, **light** (on/off/PWM), **climate**
- **safety** — gas/CO/smoke, with latching alarm semantics
- **vehicle** — ignition, motion, chassis battery

Every device also declares **capability and failure semantics**: every command has an acknowledgement topic and a timeout, every node has a `health` topic with heartbeat expectations, and automation rules declare preconditions (e.g., a heater start requires gas-OK + voltage-OK + stationary). Publishing this schema separately from the code lets hardware makers and node authors conform without reading the implementation.

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
- An npm/PyPI-style plugin registry so nodes and UI panels are one-command installs
- A hardware simulator (fake BMS, fake tank, fake CAN traffic on a virtual CAN interface) so contributors can build nodes without a van
- Remote access via Tailscale rather than custom cloud infrastructure in v1

