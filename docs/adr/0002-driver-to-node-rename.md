---
status: accepted
---

# Rename `Driver` to `Node`

The project's own vocabulary used **Driver** for "a small process (or firmware component) that talks to one specific device and normalizes its data into the shared schema" — matching Victron Venus OS's own term for the same role, since v0's first drivers were direct ports of Venus OS D-Bus drivers (`dbus-ads1115`, etc.). Every artifact carried that name: repos (`driver-tank`, `driver-battery`, `driver-relay`), Docker images, Python packages/modules (`driver_tank`, `driver_battery`), MQTT client IDs, systemd units, and `hub`'s compose service keys and env vars (`TANK_DRIVER_IMAGE_TAG`).

We renamed every one of those artifacts to **Node**: repos → `node-tank`/`node-battery`/`node-relay`, images → `ghcr.io/renewvan/node-*`, packages → `node_tank`/`node_battery`, MQTT client IDs → `node-tank`/`node-battery`/`node-battery-victron`, systemd units → `node-*.service`, compose service keys → `node-tank`/`node-battery`, env vars → `NODE_TANK_IMAGE_TAG`/`NODE_BATTERY_IMAGE_TAG`. `container_name` values (`renewvan-tank`, `renewvan-battery`) are entity-named already and were left unchanged.

## Why

Two problems with `Driver`, both increasingly proven out as the project grew past its first two entities:

- **It doesn't cover the shape the project actually needed.** The ESP32 relay firmware was never a process talking to a device over some bus — it _is_ the device, publishing directly. Calling both that firmware and the tank's ADS1115-reading process "Driver" forced either a strained definition or a second term, and `docs/architecture.md`'s Terminology section already carried a **Node** entry (originally scoped to _physical hardware_) alongside Driver, so the vocabulary had two words competing for one job the moment a firmware-only entity showed up. Collapsing to one term — **Node**: "anything that publishes one specific device or feed onto the renewvan bus, normalized into the device model" — covers firmware and process alike, and freed the old `Node` sense to become **Host** (the physical hardware a node runs on; one host may run several nodes).
- **`Driver` invites confusion with Venus OS's own driver layer**, which this project explicitly isn't part of (see `docs/porting-dbus-to-mqtt-node.md`: the whole point of the v0 native-MQTT drivers was stripping the Venus OS D-Bus/SetupHelper/GUI layers out). Keeping the same word for "the thing we ported away from" and "the thing we ported to" reads as if this project is still coupled to Venus OS's driver model, which is exactly the coupling v0 removed.

## Why this is an ADR

Hard to reverse: GitHub repo redirects survive only as long as the old names (`driver-tank`, `driver-battery`, `driver-relay`) are never re-created, and every importer of the Python packages (`driver_tank` → `node_tank`, `driver_battery` → `node_battery`) had to update in lockstep with the rename. Surprising without context: `git log` on any of the three node repos shows `driver-*` naming for most of their history, and the project previously used `Driver` as its settled term (see the superseded definition this ADR replaces). The result of a real trade-off: keeping `Driver` (process) and `Node` (hardware) as two distinct terms would have preserved `docs/architecture.md`'s original edge-nodes-run-drivers layering, at the cost of forcing two words onto things that are functionally identical from the bus's perspective — publishers of one entity's topics. We chose the single term.

## Mechanics note

`gh repo rename` keeps the same repository — tag history carries over, it isn't reset. Each node repo's pre-rename `driver-*` releases (`v0.1.0`–`v0.1.2`) already occupied those tags, so the first `node-*`-named release in each repo is `v0.1.3`, not a fresh `v0.1.0` as originally planned; the old tags are the "preserved for history" record, in the same repo rather than a separate one.

## See also

- `.scratch/driver-to-node-rename/issues/04-cutover-sequence-and-live-pi.md` — full cutover spec and execution log.
- `CONTEXT.md` — current `Node`/`Host` definitions.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md` — the pinned-image mechanism these renamed artifacts flow through.
