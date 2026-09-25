# Porting a Venus OS D-Bus driver to a native MQTT driver

Reusable recipe for turning one of the existing Venus OS D-Bus driver
packages (`dbus-ads1115`, `dbus_wattcycle_ble`, `dbus-autoterm`,
`dbus-giandel-bridge`) into a standalone native MQTT `Driver` (see
`Driver` in `CONTEXT.md`) that publishes straight to the van bus, per the
pattern decided in [dbus-ads1115 bridge interface](../.scratch/renewvan-hub-v0/issues/02-dbus-ads1115-bridge-interface.md).

Applies to **community/non-Victron hardware** — anything the project
itself owns the sensing for. Genuine Victron gear (VE.Direct/VE.Can)
stays on Venus OS and gets a remap-shaped `Driver` instead — same term,
different shape (see [Battery SoC topic mapping](../.scratch/renewvan-hub-v0/issues/04-battery-soc-topic-mapping.md)
for that counterpart), not this recipe.

Worked example throughout: `dbus-ads1115` → `renewvan/tank`.

## What to strip

- **D-Bus service registration** (`vedbus.py`) — no `VeDbusService`,
  no D-Bus object paths, no Venus OS process-monitoring integration.
- **`settingsdevice.py`** — no D-Bus-backed persistent settings; config
  moves to plain files (see "What to keep" below).
- **Venus OS packaging / SetupHelper hooks** — no `package.json` for
  SetupHelper, no `/service` daemontools directory, no GUI menu
  integration, no version/upgrade scripts tied to Venus OS's package
  manager.
- **GUI calibration screens** — Venus OS QML/GUI calibration UI doesn't
  exist outside Venus OS. v0 calibration is config-file only (see "What
  to keep"); a calibration UI is a dashboard/v1 concern, not the
  driver's.

## What to keep

- **Domain logic** — sensor read/decode, unit conversion, calibration
  math, alarm-threshold logic. This is the actual value of the original
  package and is Venus-OS-agnostic; port it near-verbatim.
- **Layered config-file pattern** — `config.default.ini` (shipped
  defaults) overridden by `config.ini` (local, gitignored). Keep this
  exactly as-is; it's a good pattern independent of Venus OS.
- **Piecewise calibration curves** (e.g. tank-shape correction tables) —
  pure math, no D-Bus dependency to begin with.

## What to add

- **An MQTT client**: connect with retry/backoff, a Last Will and
  Testament (LWT) on a `.../status` or `health`-style topic so
  downstream consumers can detect a dead driver, and a persistent
  connection (drivers publish continuously; there's no Venus-OS-style
  "subscriber required to keep publishing" quirk on this side — the
  *driver* is the source, not a re-publisher of someone else's feed).
- **A publish loop** mapping the driver's internal readings onto its
  entity's `van/<domain>/<id>/<property>` topics, per the schema in
  [Device-model v0.1 schema](../.scratch/renewvan-hub-v0/issues/01-device-model-v0.1-schema.md).
  `id` is the topic path segment itself, not a payload field.
- **Retained-topic conventions**: publish slow-changing/identity fields
  (e.g. tank's `fluid_type`, `capacity_l`) **retained**, so a new
  subscriber gets them immediately without waiting for the next change.
  Publish live/frequently-changing fields (e.g. `level_pct`) retained
  too, per ticket 01/04's decision that all v0.1 topics are retained —
  the distinction that matters is just *how often* a topic is
  republished (identity fields: once at startup, or on config reload;
  live fields: every sensor read/change), not whether it's retained.

## Service lifecycle

The driver is off Venus OS's daemontools `/service` convention; it needs
its own lifecycle:

- **Where it runs**: wherever the physical sensor is wired — for
  `renewvan/tank` that's the same Raspberry Pi 4 that hosts
  `renewvan/battery` and `renewvan/logger` (see
  [Hardware BOM](../.scratch/renewvan-hub-v0/issues/08-hardware-bom.md)),
  since the ADS1115 breakout is I2C-wired directly to that Pi's GPIO
  header. A driver for hardware wired elsewhere in the van would instead
  run on whatever host is physically nearest that hardware — the pattern
  doesn't assume one central box.
- **How it starts on boot**: a plain `systemd` unit
  (`Restart=on-failure`, `WantedBy=multi-user.target`) — no daemontools,
  no SetupHelper install hooks. One unit per driver process.

## Applying this recipe again

Two future candidates are explicitly **v1, out of v0 scope**: native-MQTT
ports of `dbus_wattcycle_ble` (Wattcycle BLE battery) and `dbus-autoterm`
(Autoterm heater), as alternative backends for users without matching
Victron hardware. When picked up, they follow this same recipe — strip
the same three D-Bus layers, keep the same domain logic and config
pattern, add the same MQTT client/publish-loop/systemd shape — against
whatever entity schema exists for battery/heater at that point.
