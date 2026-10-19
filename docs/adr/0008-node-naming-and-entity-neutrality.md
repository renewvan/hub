---
status: accepted
---

# Name nodes by source, entities by what they are

A **node** is named for the _source_ it speaks (`node-venus`, and later `node-vedirect`, `node-wattcycle`, `node-jk-bms`); an **entity** is named for what the hardware _is_ (`battery`, `solar`, `charger`) and never carries a brand. A node may publish several entities (`node-venus` publishes `battery`, `solar` and `charger`), and several nodes may publish the same entity (a Victron shunt via `node-venus` and a JK BMS via `node-jk-bms` both publish `battery`). The schema, not the node, is where vendor-neutrality lives.

Concretely, this change:

- Renamed `node-battery` to `node-venus` (repo directory, Python package `node_venus`, image `ghcr.io/renewvan/node-venus`, compose service `node-venus`, container `renewvan-venus`, health topic `renewvan/venus/health`, env vars `VICTRON_*` → `VENUS_*`). There was no compatibility shim: no external adopters existed, so nothing migrates. The repo keeps its `v0.1.0`-`v0.1.3` tags and releases (the pre-rename `driver-battery`/`node-battery` history, same as ADR 0002), and the first `node-venus` release is `v0.2.0` — a minor bump because the package, env vars and wire contract all changed. Restarting at `v0.1.0` was not possible without deleting a published release and moving its tag.
- Extended the Venus bridge from the `system/0/Batteries` aggregate to the per-service feeds (`solarcharger`, `alternator`, `charger`, per-battery `TimeToGo`/`TimeSinceLastFullCharge`).
- Added the `solar` and `charger` entities to `/schema` (`solar.schema.json`, `charger.schema.json`).
- Made `battery.temperature_c` and `battery.charge_state` optional, and added optional `time_to_go_s` and `time_since_full_charge_s`.
- Replaced the static `instance -> id` map with the Venus device instance as the entity id.

## Why

- **Per-entity nodes (`node-battery` for every battery brand) don't scale for an open project.** Every new brand would edit shared code, every user would install every driver's dependencies (BLE stack, serial, Modbus), and one bad driver would take down all batteries. A node per source keeps adding hardware a "write a node, don't touch the core" job, as `docs/architecture.md`'s plugin-first principle already says.
- **The old name was wrong, not just narrow.** `node-battery` was the Venus bridge. Someone with a JK BMS would reasonably assume it reads their BMS. Naming the node for its source removes the false promise.
- **The schema has to be the lowest common denominator, with optional extras.** A Victron SmartShunt (a real one, captured from a live Venus OS device) publishes no temperature and no charge stage, and the dashboard silently showed no battery because `isCompleteBattery` required both. An open-source schema that needs a vendor-specific field to be present excludes the simple hardware it exists to include. Required properties are only what every source can supply; consumers tolerate the rest being absent.
- **Venus device instances are stable and need no maintenance.** A static map can't work for a project that doesn't know which instances a given van has, and the user-editable `name` can change at any time.
- **A node clears a retained topic when its reading goes invalid.** Venus reports "no estimate" as `null`; leaving the last retained value would show a stale ETA. The node publishes an empty retained payload, and consumers (the logger) treat an empty payload as "no value". Venus's ~10-day cap on `TimeToGo` is a real value on the Venus screen (`9d 23h`), so it is published as-is.

## Rules this sets

- Node name = source (protocol or brand). Entity name = what it is, never a brand. Avoid `node-<entity>` names (`node-charger`, `node-booster`): they read like a vendor-neutral claim the node can't keep.
- One source per entity id. Two nodes must not publish the same `renewvan/<entity>/<id>/...`; the id is derived from the source (for `node-venus`, the Venus device instance).
- A shunt and a BMS on the same physical battery are two different views, not duplicates. When a BMS node exists it publishes its own `bms` entity (cell voltages, cell temperatures, protection flags) linked to the `battery` by id, rather than overwriting the shunt's `battery`. Fusing the two into one canonical SoC is a later, separate service.

## Why this is an ADR

Hard to reverse: the node, image, topic and env-var names are public contracts that other repos and deployments pin by name, and the optional-vs-required split of each schema property is a wire contract. Surprising without context: `git log` on `node-venus` shows `node-battery` history, and a reader will wonder why the Venus bridge is not called `node-victron` or why `battery` doesn't require `charge_state`. The result of a real trade-off: one monolithic `node-battery` would have been a smaller diff today, at the cost of every future contributor.

## See also

- `docs/adr/0002-driver-to-node-rename.md` — the previous rename, and the precedent for treating artifact names as public contracts.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md` — the pinned-image mechanism `node-venus` flows through.
- `docs/adr/0012-board-named-relay-node.md` — the one bounded exception: a Node whose hardware is a purchasable relay board is named by host and board. This ADR's source-naming rule still governs every sensing Node.
- `CONTEXT.md` — `Battery`, `Solar`, `Charger` and `Node` definitions.
