---
status: accepted
---

# Demo mode publishes a simulated van on a separate topic root

Demo mode is a simulated van published on the renewvan bus by a dedicated `node-demo`, under its own topic root `renewvan-demo/` instead of `renewvan/`. **Demo mode** is one switch shared by every client on the bus: any client publishes `renewvan-demo/enabled/set`, `node-demo` (always running, idle while off) answers with the retained `renewvan-demo/enabled` and starts or clears the simulated van, and every dashboard follows that flag. Real nodes keep running untouched.

It is modelled on Venus OS's Demo mode (`Settings → General → Demo mode`, setting `/Settings/Gui/DemoMode`, with the scenarios ESS / Boat-Motorhome 1 / Boat-Motorhome 2). Venus implements it with `victronenergy/dbus-recorder`'s `play.py`, which replays a recording (CSV/pickle of real services' D-Bus values) under the **same** D-Bus service names the real hardware uses, loops it, and resets to the recording's initial state at the end of each loop. Its `startdemo.sh` first stops the real serial, VE.Direct and GPS services so the replay does not collide with live sources.

Concretely, this change:

- Added the `renewvan-demo/<entity>/<id>/<property>` topic root: same property names, same `json.dumps(value)` payload, retained, qos 1, and `renewvan-demo/health` (`online`/`offline`, LWT, retained). A consumer reads demo data with the same code and swaps only the root.
- Added the shared switch: `renewvan-demo/enabled/set` (command, not retained) and `renewvan-demo/enabled` (state, retained), the same `<topic>` / `<topic>/set` convention as the kiosk settings.
- Added `node-demo`, a node that replays scenarios from this repo's `schema/demo/` (`<entity>[.<name>].demo.json`, envelope `schema/demo.schema.json`) on one shared clock: `initial` published at loop start, `timeline` steps overlaying `set`, reset to `initial` at `loop_s`.
- Added `schema/validate-demo.py`, which validates the envelope, each scenario's `initial` and every cumulative `initial ⊕ set` against the entity schema, and fails when an entity has no scenario.
- Made a demo scenario part of the definition of done for a new entity (or a new required property on an existing one). A new node for an existing entity ships none (`docs/porting-dbus-to-mqtt-node.md`, "Adding a node for an existing entity").

The full contract is `docs/demo-mode.md`.

## Why

- **Replaying onto the real topics would break the bus.** Venus can replay under the real service names because it stops the real services first. renewvan nodes publish _retained_ topics on `renewvan/`, so demo values would overwrite real state and outlive the demo, and ADR 0008's one-source-per-entity-id rule would be violated by construction.
- **The logger would record fake data.** It subscribes `renewvan/#` and writes to InfluxDB. Demo data on a separate root never matches that subscription, so no change to the logger is needed and fake readings cannot reach history.
- **A consumer-side root switch replaces stopping the real sources.** Stopping nodes means a Pi-side action, and then restoring them. Choosing which root a client reads is instant, reversible, and touches no real node.
- **Scenarios live in the hub, not in each node repo.** Per-node-repo fixtures would force `node-demo` to import from every node repo, contradicting ADR 0001's pinned, independently-published images, and the fixtures would drift from the schema. In `schema/demo/` they sit next to the schemas they must satisfy, and `validate-demo.py` fails the hub's own check when they drift. The scenario is per entity, not per source, because the dashboard cannot tell sources apart (ADR 0008).
- **The switch is a retained bus flag, not a per-client preference.** The first design kept it in each client's `localStorage` and required `node-demo` to be started by hand. That fails the real use: turning demo mode on from one dashboard must make the data available to every other client, without anyone running a command on the Pi. A retained `enabled` flag is the one state all clients and `node-demo` can share, it survives a reload and a restart, and it needs no new channel because the kiosk settings already use `<topic>` / `<topic>/set`. The cost is that the switch does nothing when `node-demo` is not running, and that a laptop with no hub no longer works (it needs a broker with `node-demo` on it).

## Rules this sets

- Demo data is published only under `renewvan-demo/`. Nothing simulated is ever published on `renewvan/`.
- Demo mode does **not** stop real sources, deliberately. With demo on, real telemetry keeps flowing on `renewvan/` and is simply ignored by the dashboard. The consequence: the logger keeps recording real data while someone demos the dashboard.
- `node-demo` always runs and is idle while off: its compose service has no profile, and it publishes nothing until `renewvan-demo/enabled` is `true`. A real van is never shown fake data unless someone switches it on, and the switch defaults to off (no retained value means off).
- `node-demo` must never wait for broker acknowledgements on paho's network thread, which is also the thread that receives them: that deadlocks until the timeout and nothing is cleared. `/set` only queues the request and the driver thread applies it. This was found by testing against a real broker, not by unit tests with a fake publisher.
- Demo state is read-only. `node-demo` subscribes to nothing but `renewvan-demo/enabled` and `renewvan-demo/enabled/set`, never to an entity's `<field>/set`; while the switch is on, the dashboard publishes only kiosk commands and the switch itself.
- A new entity schema, or a new required property on one, ships with its demo scenario in the same change.

## Rejected alternatives

- **Each node gains a `DEMO` env flag** and publishes fake values itself. It cannot be flipped from the dashboard (it needs a container restart or an extra command channel) and it would publish fake values onto the real, retained `renewvan/` topics, the exact collision described above.
- **An in-browser simulator only.** It is the cheapest toggle, but it never exercises the broker path, the retained-topic behaviour, or any other consumer (logger, Grafana, a second dashboard), so it demonstrates less than a real bus does.

## Why this is an ADR

Hard to reverse: the `renewvan-demo/` root is a wire contract that the dashboard and any other consumer subscribe to by name, and the scenario format is a schema other repos validate against. Surprising without context: Venus OS replays under the real service names and stops real sources, so a reader will wonder why renewvan uses a second topic root and leaves real nodes running. The result of a real trade-off: replaying on `renewvan/` would have been the closest copy of Venus and the smaller diff, at the cost of overwriting retained real state and polluting InfluxDB.

## See also

- `docs/demo-mode.md` — the contract: topics, scenario format, per-node requirement.
- `docs/adr/0008-node-naming-and-entity-neutrality.md` — one source per entity id; the scenario is per entity.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md` — why scenarios cannot live in per-node repos.
- `CONTEXT.md` — `Demo mode`, `Demo root`, `Demo scenario` and `node-demo` definitions.
