# Demo mode

A simulated van on the renewvan bus, so the dashboard (and any other consumer) can be built and shown without a van, a Pi, or real hardware. Modelled on Venus OS's Demo mode (`Settings → General → Demo mode`), which replays recordings of real services through `dbus-recorder`'s `play.py` under the same D-Bus service names the real hardware uses, and stops the real sources while it runs (`startdemo.sh`).

## Contract

**Separate topic root.** Demo data is published under `renewvan-demo/`, never `renewvan/`. The property names, JSON encoding, retained flag and `<entity>/<id>/<property>` shape are identical to the real bus, so a consumer reads demo data with the same code and only swaps the root. Reasons it is not published on `renewvan/`:

- Real nodes publish _retained_ topics there (ADR 0008: one source per entity id). Demo values would overwrite real state and outlive the demo.
- The logger subscribes `renewvan/#`; fake readings must never reach InfluxDB.

| Topic                                    | Direction         | Payload                                                          |
| ---------------------------------------- | ----------------- | ---------------------------------------------------------------- |
| `renewvan-demo/<entity>/<id>/<property>` | `node-demo` → bus | same JSON value as `renewvan/<entity>/<id>/<property>`, retained |
| `renewvan-demo/health`                   | `node-demo` → bus | bare `online` / `offline` (not JSON-quoted; LWT), retained       |
| `renewvan-demo/router/health`            | `node-demo` → bus | bare `online` while a router scenario exists; cleared on stop    |

`node-demo` is opt-in (compose profile `demo`), publishes nothing real, and costs one idle MQTT connection plus a 1 Hz tick while running. The dashboard's **Demo mode** toggle is kiosk-local (`localStorage`, like the Alerts toggle) and only chooses which root the dashboard subscribes to (`renewvan/#` or `renewvan-demo/#`). It does not start or stop anything on the Pi, so toggling is instant, and it works from a laptop with no hub. With `node-demo` not running, the demo root is simply empty.

## Scenarios

One file per simulated entity instance under [`schema/demo/`](../schema/demo/), `<entity>[.<name>].demo.json`, enveloped by [`schema/demo.schema.json`](../schema/demo.schema.json):

```json
{
  "entity": "battery",
  "id": "demo",
  "description": "…",
  "loop_s": 120,
  "initial": { "soc_pct": 78.0, "voltage_v": 13.2, "current_a": 18.5, "power_w": 244.0 },
  "timeline": [{ "t": 20, "set": { "soc_pct": 78.4 } }]
}
```

- `initial` is one complete entity record, published retained at loop start (Venus: the recording's initial state block).
- Each `timeline` step overlays `set` at `t` seconds into the loop (Venus: the timestamped change rows). Only changed properties are published.
- At `loop_s` the scenario resets to `initial` and restarts (Venus: `play.py` resets to the start state and restarts the event stream).
- Every scenario shares one clock, so entities stay coherent (solar rising while the battery charges).
- Values are realistic and schema-valid. Prefer values captured from a real device (`node-venus/tests/fixtures/venus_capture_*.json`) over invented ones.

`schema/validate-demo.py` checks the envelope, validates `initial` and every cumulative `initial ⊕ set` against the entity schema, and fails if an entity has **no** scenario.

## Requirement for every node

An entity is not done until it has a demo scenario. Concretely, a change that adds a `schema/<entity>.schema.json` (new node, new entity) or adds a **required** property to an existing one MUST, in the same change:

1. add or update `schema/demo/<entity>[.<name>].demo.json` so `python3 schema/validate-demo.py` passes;
2. keep the scenario exercising each state a consumer must handle: at minimum the optional-absent case (a source that omits optional properties) and, where the entity has alarm/fault semantics, one step that enters it (a tank crossing its threshold, a router signal dip).

`validate-demo.py` enforces (1) mechanically; (2) is a review item. A new _node_ for an existing entity (another BMS publishing `battery`) needs no new scenario: the scenario is per entity, not per source, because the dashboard cannot tell sources apart (ADR 0008).

Command topics (`<field>/set`, e.g. temperature `name`/`unit`) are not simulated by `node-demo`: demo state is read-only. The dashboard disables or no-ops its writes in demo mode.

## Run it

```sh
# from the hub repo, with a broker on localhost:1883
# `demo` is a compose profile: a plain `docker compose up -d` on a real van never starts a fake one
docker compose --profile demo up -d mosquitto node-demo
```

Then in the dashboard: Settings → General → Demo mode.
