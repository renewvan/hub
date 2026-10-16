# Demo mode

A simulated van on the renewvan bus, so the dashboard (and any other consumer) can be built and shown without a van, a Pi, or real hardware. Modelled on Venus OS's Demo mode (`Settings → General → Demo mode`), which replays recordings of real services through `dbus-recorder`'s `play.py` under the same D-Bus service names the real hardware uses, and stops the real sources while it runs (`startdemo.sh`).

## Contract

**A namespace inside the real root.** Demo data is published under `renewvan/demo/`, one level inside `renewvan/`, and never on a real entity topic. The property names, JSON encoding, retained flag and `<entity>/<id>/<property>` shape are identical to the real bus, so a consumer reads demo data with the same code and only adds the `demo/` segment. `demo` is not an entity domain, so nothing that recognises `renewvan/<domain>/<id>/<property>` can read a demo topic as a real entity. Reasons it is not published on the real entity topics:

- Real nodes publish _retained_ topics there (ADR 0008: one source per entity id). Demo values would overwrite real state and outlive the demo.
- The logger subscribes `renewvan/#`; fake readings must never reach InfluxDB.

**Consequence of sharing the root.** Anything that subscribes `renewvan/#` (a plain `mosquitto_sub`, Grafana's MQTT source, the dashboard, the logger) also receives `renewvan/demo/#`. The logger is safe by construction: it only writes four-segment topics whose domain is an entity, and a demo entity topic has five segments (`renewvan/demo/<entity>/<id>/<property>`) while every other demo topic has `demo` in the domain slot. That is pinned by a regression test in `logger`. Any other consumer that writes or acts on bus data must skip the `demo/` segment itself.

| Topic                                    | Direction         | Payload                                                                                        |
| ---------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------- |
| `renewvan/demo/enabled/set`              | client → bus      | JSON `true` / `false`, not retained: the command                                               |
| `renewvan/demo/enabled`                  | `node-demo` → bus | JSON `true` / `false`, retained: the shared state every client follows                         |
| `renewvan/demo/<entity>/<id>/<property>` | `node-demo` → bus | same JSON value as `renewvan/<entity>/<id>/<property>`, retained, only while on; five segments |
| `renewvan/demo/health`                   | `node-demo` → bus | bare `online` / `offline` (not JSON-quoted; LWT), retained                                     |
| `renewvan/demo/router/health`            | `node-demo` → bus | bare `online` while on and a router scenario exists; cleared when switched off                 |

**One shared switch.** `enabled` / `enabled/set` follow the same `<topic>` / `<topic>/set` convention as the kiosk settings. Any client publishes the command; `node-demo` applies it, republishes the retained state, and starts or clears the simulated van. Every dashboard follows the retained `enabled`, so toggling it in one client shows the demo in all of them, and it survives a reload and a `node-demo` restart. A client never flips its own view: it asks, and the view changes when the bus reports the change. With no `node-demo` running the command is never answered and the switch does nothing.

`node-demo` always runs (compose service, no profile) and is idle while off: one MQTT connection and a 1 Hz tick that publishes nothing. It must not wait for broker acknowledgements on paho's network thread (which also receives them), so `/set` only queues the request and the 1 Hz driver thread applies it, immediately.

State rules:

- **Boot.** A retained `enabled: true` resumes the demo from every scenario's initial state (a crash can leave retained topics mid-loop; the first tick republishes every property). No retained value means off.
- **Re-enabling** restarts every scenario at its beginning. A repeated `true` does not restart a running demo.
- **Switching off** clears every simulated topic (an empty retained payload, ADR 0008's "no value") and `router/health`, but leaves `enabled: false` retained.
- **Clean shutdown** clears the van but leaves `enabled` as it was, so a Pi that reboots while in demo mode comes back in demo mode.
- **Invalid payloads** (anything but a JSON boolean) are ignored and logged.
- **Clean MQTT session.** All state is in retained topics; a persistent session would replay a `/set` queued while the node was down.

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

`node-demo` is part of the compose stack and starts with it; there is nothing to start by hand. Turn demo mode on from any dashboard: Settings → General → System → Demo mode. To drive it without a dashboard:

```sh
mosquitto_pub -t renewvan/demo/enabled/set -m true    # on, for every client
mosquitto_pub -t renewvan/demo/enabled/set -m false   # off
mosquitto_sub -t renewvan/demo/enabled -v             # current shared state (retained)
```
