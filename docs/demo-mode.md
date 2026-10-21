# Demo mode

A simulated van on the renewvan bus, so the dashboard (and any other consumer) can be built and shown without a van, a Pi, or real hardware. Modelled on Venus OS's Demo mode (`Settings → General → Demo mode`), which replays recordings of real services through `dbus-recorder`'s `play.py` under the same D-Bus service names the real hardware uses, and stops the real sources while it runs (`startdemo.sh`).

## Contract

**A namespace inside the real root.** Demo data is published under `renewvan/demo/`, one level inside `renewvan/`, and never on a real entity topic. The property names, JSON encoding, retained flag and `<entity>/<id>/<property>` shape are identical to the real bus, so a consumer reads demo data with the same code and only adds the `demo/` segment. `demo` is not an entity domain, so nothing that recognises `renewvan/<domain>/<id>/<property>` can read a demo topic as a real entity. Reasons it is not published on the real entity topics:

- Real devices publish _retained_ topics there (ADR 0008: one source per entity id). Demo values would overwrite real state and outlive the demo.
- The logger subscribes `renewvan/#`; fake readings must never reach InfluxDB.

**Consequence of sharing the root.** Anything that subscribes `renewvan/#` (a plain `mosquitto_sub`, Grafana's MQTT source, the dashboard, the logger) also receives `renewvan/demo/#`. The logger is safe by construction: it only writes four-segment topics whose domain is an entity, and a demo entity topic has five segments (`renewvan/demo/<entity>/<id>/<property>`) while every other demo topic has `demo` in the domain slot. That is pinned by a regression test in `logger`. Any other consumer that writes or acts on bus data must skip the `demo/` segment itself.

| Topic                                    | Direction           | Payload                                                                                                         |
| ---------------------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------- |
| `renewvan/demo/enabled/set`              | client → bus        | JSON `true` / `false`, not retained: the command                                                                |
| `renewvan/demo/enabled`                  | `driver-demo` → bus | JSON `true` / `false`, retained: the shared state every client follows                                          |
| `renewvan/demo/<entity>/<id>/<property>` | `driver-demo` → bus | same JSON value as `renewvan/<entity>/<id>/<property>`, retained, only while on; five segments                  |
| `renewvan/demo/health`                   | `driver-demo` → bus | bare `online` / `offline` (not JSON-quoted; LWT), retained                                                      |
| `renewvan/demo/<device>/info`            | `driver-demo` → bus | Device info JSON from the fleet file, retained, only while on; three segments; absent for a Device with no info |
| `renewvan/demo/<device>/health`          | `driver-demo` → bus | bare `online` / `offline` per the fleet file, retained, only while on; cleared when switched off                |

**One shared switch.** `enabled` / `enabled/set` follow the same `<topic>` / `<topic>/set` convention as the kiosk settings. Any client publishes the command; `driver-demo` applies it, republishes the retained state, and starts or clears the simulated van. Every dashboard follows the retained `enabled`, so toggling it in one client shows the demo in all of them, and it survives a reload and a `driver-demo` restart. A client never flips its own view: it asks, and the view changes when the bus reports the change. With no `driver-demo` running the command is never answered and the switch does nothing.

`driver-demo` always runs (compose service, no profile) and is idle while off: one MQTT connection and a 1 Hz tick that publishes nothing. It must not wait for broker acknowledgements on paho's network thread (which also receives them), so `/set` only queues the request and the 1 Hz driver thread applies it, immediately.

State rules:

- **Boot.** A retained `enabled: true` resumes the demo from every scenario's initial state (a crash can leave retained topics mid-loop; the first tick republishes every property). No retained value means off.
- **Re-enabling** restarts every scenario at its beginning. A repeated `true` does not restart a running demo.
- **Switching off** clears every simulated topic (an empty retained payload, ADR 0008's "no value"), including every simulated Device's `info` and `health`, but leaves `enabled: false` retained.
- **Clean shutdown** clears the van but leaves `enabled` as it was, so a Pi that reboots while in demo mode comes back in demo mode.
- **Invalid payloads** (anything but a JSON boolean) are ignored and logged.
- **Clean MQTT session.** All state is in retained topics; a persistent session would replay a `/set` queued while the device was down.

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
- Values are realistic and schema-valid. Prefer values captured from a real device (`driver-venus/tests/fixtures/venus_capture_*.json`) over invented ones.

`schema/validate-demo.py` checks the envelope, validates `initial` and every cumulative `initial ⊕ set` against the entity schema, and fails if an entity has **no** scenario. It also runs the [fleet pass](#simulated-devices-the-fleet-file).

### Simulated Devices: the fleet file

The simulated Devices are declared in one hub-owned file, [`schema/demo/fleet.demo-fleet.json`](../schema/demo/fleet.demo-fleet.json), enveloped by [`schema/demo-fleet.schema.json`](../schema/demo-fleet.schema.json). It is named `*.demo-fleet.json` so the per-entity glob (`*.demo.json`) never matches it, and the entity validation is unchanged. `driver-demo` is a simulator, not a Device (`CONTEXT.md`): it has no manifest, info or entry of its own. This file is the contract; the replay of the fleet is a `driver-demo` change that follows this one, so until it ships `driver-demo` still publishes only the router's health topic.

```json
{
  "description": "…",
  "loop_s": 120,
  "devices": [
    {
      "device": "venus",
      "health": "online",
      "info": { "device": "venus", "version": "v0.0.0-demo", "…": "…" }
    },
    { "device": "gps", "health": "online" },
    {
      "device": "router",
      "health": "online",
      "health_timeline": [
        { "t": 60, "health": "offline" },
        { "t": 90, "health": "online" }
      ],
      "info": { "device": "router", "…": "…" }
    }
  ]
}
```

- `info` is a complete [Device info](../schema/device-info.schema.json), published retained at loop start. **Omit it** to simulate a Device that publishes no info (health only).
- `health` is the bare retained word at loop start. `offline` with an `info` present is a Device that dropped and left its retained info.
- `health_timeline` overlays health at `t` seconds into the loop; health resets to `health` at each loop start. Exactly one Device has one, so the live online-to-offline-to-online transition is on display. The shipped one is the router, dropping out as its signal bottoms out (`router.demo.json`, t=60) and returning as it recovers (t=90).
- Shares one clock and `loop_s` with the entity scenarios.
- The shipped fleet shows every Device-page state: a healthy multi-entity Device (`venus`), a `dev` build (`tank`), entities with no info (`gps`), an offline Device with retained info (`tilt`), an ESP32 with a MAC serial (`esp32-relay`), and the one flapping Device (`router`).
- **Versions and serials are visibly fake** (`v0.0.0-demo`, `DEMO…`, `DE:AD:BE:EF:…`) and never read from the Pi, so a screenshot of the demo cannot leak a real serial.

The fleet pass of `validate-demo.py` checks: the envelope; each `info` against `device-info.schema.json` with `info.device` equal to the entry's `device`; the reserved key `demo` is rejected (by the device-key pattern, which must stay identical in `demo-fleet.schema.json` and `device-info.schema.json`, so it cannot drift from the real contract); every `publishes` type has an entity scenario; `health_timeline` times are non-decreasing and `< loop_s`; exactly one Device flaps; versions and serials are visibly fake (`dev` or `*-demo`; a `DEMO…` serial or a unicast, locally administered MAC, which a vendor does not assign to a real device); and the fleet includes every state listed above plus the `router`.

## Requirement for every device

An entity is not done until it has a demo scenario. Concretely, a change that adds a `schema/<entity>.schema.json` (new device, new entity) or adds a **required** property to an existing one MUST, in the same change:

1. add or update `schema/demo/<entity>[.<name>].demo.json` so `python3 schema/validate-demo.py` passes;
2. keep the scenario exercising each state a consumer must handle: at minimum the optional-absent case (a source that omits optional properties) and, where the entity has alarm/fault semantics, one step that enters it (a tank crossing its threshold, a router signal dip).

`validate-demo.py` enforces (1) mechanically; (2) is a review item. A new _device_ for an existing entity (another BMS publishing `battery`) needs no new scenario: the scenario is per entity, not per source, because the dashboard cannot tell sources apart (ADR 0008).

A new **required** property on the Device info schema (`schema/device-info.schema.json`) likewise needs a fleet update in the same change: every entry's `info` in `schema/demo/fleet.demo-fleet.json` must still validate, and `validate-demo.py` fails until it does. A new entity type also needs it listed in the `publishes` enum of both Device schemas (`schema/validate-examples.sh` enforces that) and, for the dashboard to show it under a Device, a fleet Device that publishes it.

Command topics (`<field>/set`, e.g. temperature `name`/`unit`, relay `set` and `name/set`) are not simulated by `driver-demo`: demo state is read-only. The dashboard disables or no-ops its writes in demo mode. This matters most for relays: the demo ids (`relay_01`…`relay_05`) overlap the real ids, so a consumer must never publish a relay `set` or `name/set` while Demo mode is on (`docs/adr/0013-writable-relay-and-its-wire-contract.md`).

## Run it

`driver-demo` is part of the compose stack and starts with it; there is nothing to start by hand. Turn demo mode on from any dashboard: Settings → General → System → Demo mode. To drive it without a dashboard:

```sh
mosquitto_pub -t renewvan/demo/enabled/set -m true    # on, for every client
mosquitto_pub -t renewvan/demo/enabled/set -m false   # off
mosquitto_sub -t renewvan/demo/enabled -v             # current shared state (retained)
```
