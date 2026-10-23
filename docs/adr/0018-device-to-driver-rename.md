---
status: accepted
---

# Rename `Device` to `Driver`

The project's word for "the software that publishes one specific source onto the renewvan bus" changes from **Device** to **Driver**. Every artifact derived from the old word changes with it, as a clean cutover with no alias or shim. This is the third name for the same concept (Driver to Node in ADR 0002, Node to Device in ADR 0014, Device to Driver here); the mechanics are those of ADR 0014.

| Was                                                                                                               | Now                                                                                                                      |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Glossary: Device, Device manifest, Device info, Device page, Device info page, Device row                         | Driver, Driver manifest, Driver info, Driver page, Driver info page, Driver row                                          |
| JSON key `device` (manifest, info, fleet entry)                                                                   | `driver`                                                                                                                 |
| Fleet key `devices[]`                                                                                             | `drivers[]`                                                                                                              |
| `schema/device-manifest.schema.json`, `schema/device-info.schema.json` (`$id`, `title`)                           | `schema/driver-manifest.schema.json`, `schema/driver-info.schema.json`                                                   |
| `schema/examples/device-*.json`                                                                                   | `schema/examples/driver-*.json`                                                                                          |
| Python `device_info.py` and its tests                                                                             | `driver_info.py`                                                                                                         |
| `DEVICE_VERSION` build arg / env var; the release workflow input `device-version-arg`                             | `DRIVER_VERSION`; `driver-version-arg`                                                                                   |
| INI section `[device]` in `config.ini*`                                                                           | `[driver]`                                                                                                               |
| `docs/authoring-a-device.md`, `docs/porting-dbus-to-mqtt-device.md`                                               | `docs/authoring-a-driver.md`, `docs/porting-dbus-to-mqtt-driver.md`                                                      |
| Dashboard `state.devices`, `DeviceRow`, `deviceRows`, `DeviceView`, `DeviceInfoView`, `DevicesView`, `deviceTone` | `state.drivers`, `DriverRow`, `driverRows`, `DriverView`, `DriverInfoView`, `DriversView`, `driverTone`                  |
| Dashboard settings views `devices` / `device` / `device-info` and the URL parameter `device=`                     | `drivers` / `driver` / `driver-info` and `driver=`; the titles in the UI read **Drivers** and **Driver**                 |
| `driver-wattcycle`'s key `battery` (`renewvan/battery/info`, `renewvan/battery/health`)                           | `wattcycle` (`renewvan/wattcycle/info`, `renewvan/wattcycle/health`); its entity topics stay `renewvan/battery/<id>/...` |

Topics do not move: `renewvan/<key>/info` and `renewvan/<key>/health` keep the same `<key>`, and only the payload field `device` becomes `driver`. The one key that does change, `battery` to `wattcycle`, is a correction (below), not part of the rename.

Unchanged on purpose: the "device model" (the schema set as a whole, as in ADR 0014); every use of "device" that is not this project's word (a Venus OS "device instance" and `DeviceInstance`, a Bluetooth, evdev, USB or block device, `/dev/...`, ESPHome's own wording when quoted, React and DOM `device*` APIs, the Home Assistant "device block", `deviceMemory`, MQTT `client_id`); **Board**, **Entity**, **Plugin**, **Hub plugin**, **Kiosk plugin**; the artifact prefix `driver-<source>` and the compose service names and image tags, which already said `driver-*`.

## Why

- **The Settings list showed software, and the word read as hardware.** Settings › Devices lists what publishes: a name, a version, its health, which Board it runs on, what it publishes. A user reads "Device" as the physical thing (the battery, the router), which the row is not. The same word is also what Venus OS, Home Assistant and Zigbee2MQTT use for hardware, so it collided with them in conversation and in the UI.
- **Concept and artifact now agree.** After ADR 0014 every repo, image, package and service was already `driver-<source>` while the concept was "Device", and `CONTEXT.md` had to carry an Avoid entry explaining the mismatch. With **Driver** the prefix is simply the name of the thing; the one repo that wears the prefix without being one is `driver-demo`, a simulator (see below).
- **Two sources for one bank need the publisher named on the wire.** `driver-venus` and `driver-wattcycle` both publish the same physical battery bank, and entity instances now name their owning Driver (ADR 0019). Writing `driver` as the property reads correctly; `device` there would have said "the hardware", which is exactly the opposite.
- **ADR 0002's objection is acknowledged and accepted.** ADR 0002 renamed Driver to Node partly because the ESP32 relay firmware is itself the device, and "driver" sounded like something that sits in front of hardware. That is still true of `esp32`: its firmware is a Driver that is also its own hardware, and the Board it runs on is the relay board. This ADR accepts that one concept covers both the Pi process in front of a sensor and firmware that is the sensor, because what unites them is what they do (publish a source onto the bus), not where they run.

## Rules this sets

- A Driver's key is its repo name without the `driver-` prefix (`driver-venus` is Driver `venus`, `driver-wattcycle` is Driver `wattcycle`). `driver-esp32` follows it too (Driver `esp32`, ADR 0020). It is the `<driver>` segment of `renewvan/<driver>/info` and `renewvan/<driver>/health` and the value of the `driver` property on its entity instances. The slug rule still rejects a `driver-` prefix and the reserved key `demo`.
- **driver-demo is a simulator, not a Driver.** It owns no hardware, remaps no feed, and has no manifest or info. It keeps its artifact name for historical reasons only; renaming the repo was judged not worth a fifth coordinated repo rename. The glossary says so explicitly, and the Demo fleet is the simulated list of Drivers.
- "Device" in the glossary now means hardware in general (the thing a Driver reads or runs on a Board of). It is never the publisher.

## Supersedes (naming only)

Like ADR 0014 for its predecessors, this ADR supersedes **only the naming** of ADR 0014. ADRs 0002, 0008, 0011, 0012 and 0014 are historical records and are left as written, so they still say Node, Host and Device. Read them with this table, ADR 0014's table, and the rule that "Device" there means "Driver" here. Their decisions stand: source naming (0008), the manifest and retained info topic (0011), the board-named relay (0012), and the Node-to-Device vocabulary mechanics (0014). ADRs 0015 to 0017, which are recent, have their wording updated in place.

## Why this is an ADR

Hard to reverse: the payload key, the schema file names and `$id`s, the INI section and the build-arg name are public contracts that thirteen repos pin by name, and every driver repo's CI fetches the manifest schema at a pinned ref. Surprising without context: the accepted ADRs still say Device (and Node), the artifact prefix has said `driver-` throughout, and `driver-demo` is named a Driver without being one. The result of a real trade-off: keeping "Device" cost nothing, and renaming costs a coordinated change across every repo to remove an ambiguity users actually hit.

## Cutover

No compatibility shim, as in ADR 0014. Every running van is redeployed with all repos together, because a payload key changed on the wire.

1. **Clear the old retained `info`.** A broker that still holds a retained `renewvan/<key>/info` with the key `device` fails the new schema. Clear them once: `mosquitto_pub -r -n -t 'renewvan/<key>/info'` for each key, or `mosquitto_sub -v -t 'renewvan/+/info' --retained-only -W 2` to list them first. Drivers republish a valid `info` on their next connect.
2. **Clear the retired `battery` Driver key.** `driver-wattcycle` published under key `battery` (`renewvan/battery/info`, `renewvan/battery/health`), against ADR 0008 (a Driver is named for its source). Its new key is `wattcycle`. Run `mosquitto_pub -r -n -t renewvan/battery/info` and `mosquitto_pub -r -n -t renewvan/battery/health` once, or the retired Driver stays `online` on the Drivers page.
3. **Edit the live INI.** Config files on the Pi (`/etc/renewvan/<driver>/config.ini`) keep their `[device]` section until edited: change it to `[driver]` by hand (for `wattcycle` the `mac` key sits there, and the hub's `bin/deploy.sh` leaves that driver off until it is set). The seeded defaults in `docker/<driver>/config.ini.default` are already `[driver]`, but deploy never overwrites an existing file.
4. **Rename the env var.** Any local build or script that sets `DEVICE_VERSION` sets `DRIVER_VERSION`. In the hub's reusable release workflow the input is `driver-version-arg` (was `device-version-arg`), so a caller's `with:` block changes with it.
5. **Bump the CI pins.** Every driver repo's Manifest check fetches the manifest schema at a pinned `hub-ref`; the schema moved from `schema/device-manifest.schema.json` to `schema/driver-manifest.schema.json` and its `device` key is now `driver`, so each repo's `manifest.json` and its `HUB_REF` pin change together in one commit, and the workflow reference in the caller's `uses:` is bumped to the same hub SHA (ADR 0015).
6. **Dashboard URLs.** A bookmarked `?device=` settings URL no longer resolves; the parameter is `driver=`.

## See also

- `docs/adr/0002-driver-to-node-rename.md`, `docs/adr/0014-node-to-device-rename.md`: the earlier renames of the same concept.
- `docs/adr/0019-entity-instances-name-their-driver-and-shared-primary-battery.md`: the instance attribution this rename makes readable.
- `docs/adr/0020-one-driver-esp32-repo-many-firmware-variants.md`: the Driver key `esp32` for the whole ESP32 repo.
- `docs/adr/0015-one-release-process-via-reusable-workflows.md`: the workflow input and the pinned-ref rule the cutover uses.
- `CONTEXT.md`: **Driver**, **Driver manifest**, **Driver info**, **driver-demo**, **Driver artifact naming**.
