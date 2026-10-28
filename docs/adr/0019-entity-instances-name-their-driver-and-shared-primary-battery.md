---
status: accepted
---

# Entity instances name their Driver, and the Primary battery is shared

Two decisions that arrive together because the second needs the first.

1. **Every entity instance may name the Driver that publishes it.** All nine entity schemas (`battery`, `solar`, `charger`, `tank`, `relay`, `router`, `gps`, `temperature`, `tilt`) gain two optional retained properties: `driver` (the publishing Driver's key, topic `renewvan/<entity>/<id>/driver`, a JSON string, same slug rule as `driver-info.schema.json`) and `name` (a human label, topic `renewvan/<entity>/<id>/name`, a non-empty JSON string). `relay` and `temperature` already had `name`; its meaning is unchanged and only `driver` is new there.
2. **The headline battery is a shared setting.** One physical bank can be published by two Drivers (`driver-venus` reads the SmartShunt through Venus OS, `driver-wattcycle` reads the BMS over BLE), so the dashboard must pick one instance. The pick is stored on the bus, not in a client: the retained topic `renewvan/hub/primary-battery` carries `"auto"` or a battery instance id, a client changes it by publishing the same payload, unretained, to `renewvan/hub/primary-battery/set`, and the **Hub plugin** (`plugins/hub`, systemd `renewvan-hub`) owns it: it persists the value on the Pi, republishes it retained on start and on every (re)connect, defaults to `"auto"`, and ignores a payload that is not a non-empty JSON string. The contract is `schema/hub-primary-battery.schema.json`.

Consumers must tolerate `driver` being absent. Without it an instance is attributed to every Driver whose Driver info `publishes` that entity type (the legacy join, unchanged from ADR 0011), and a Driver that does not publish it stays valid. Every in-repo Driver should publish it, at the same moment as the instance's other retained identity fields (startup, every (re)connect, and when a dynamic instance is first discovered).

The dashboard reads `auto` as deterministic: sort the instance ids and take the first complete battery, never arrival order. A specific id means that instance when it is on the bus and complete, otherwise the dashboard falls back to `auto` and says so in the UI. Demo mode reads and writes the same topic and falls back when the id is not a demo instance.

## Why

- **The wire did not say who owned an instance.** ADR 0011 left "two Drivers publishing the same entity type both show the same instances in a consumer that joins by `publishes`. Per-instance attribution is an accepted limit, not a bug". That was fine with one source per entity. With `driver-venus` and `driver-wattcycle` both publishing `battery`, the Drivers page showed each of them owning both instances, and nothing said which instance came from which source. This ADR supersedes that one rule: attribution is now a property of the instance, and the join by `publishes` remains only as the legacy fallback.
- **Per-instance and optional.** The owner is a fact about the instance, so it lives with the instance's other retained identity fields and is published by the same code at the same moment. Optional keeps `additionalProperties: false` schemas, old Drivers and old retained state valid during a staggered rollout.
- **A battery is not fused.** ADR 0008 says a shunt and a BMS on the same physical battery are two different views, not duplicates, and that fusing them into one canonical reading is a later, separate service. That is respected: both stay separate `battery` instances with their own ids, the user chooses which one is the headline, and nothing averages or merges them (selection, not fusion). Per-cell BMS data was planned as a future `bms` entity here; ADR 0022 reverses that and puts it on `battery`.
- **Why shared, and why the hub plugin.** The headline battery changes what every screen says: the kiosk, a phone and a laptop must agree, or the same van shows two state-of-charge numbers. A client-local choice (localStorage, or a kiosk-only setting) would diverge per client and be lost with a browser profile. The hub plugin is the existing Pi-side owner of hub-wide state that survives a restart, and the command-then-state shape (`.../set` unretained, owner republishes retained state) is the convention the kiosk plugin already uses for `brightness`. The plugin does not know which instances exist, so it validates only the payload's shape; checking an id against the bus is the consumer's job and the fallback to `auto` is the answer to a stale id.

## Rejected alternatives

- **Driver info lists its instances.** Add `instances: [{entity, id}]` to the info payload. Rejected: info is published once per connect while instances come and go (a tank configured later, a Venus device that appears), so the list would be stale or force a republish of info for every change, and it duplicates what the entity topics already say.
- **Encode the Driver in the id** (`wattcycle:512`, a prefix, a topic segment). Rejected: it changes the topic of every existing instance, breaks the logger's and dashboard's keys and the persisted history, and makes the id mean two things (ADR 0008 derives the id from the source).
- **Make `driver` required.** Cleaner join. Rejected: it would invalidate every Driver that has not shipped it yet and every retained instance on a broker, forcing one coordinated release across all Driver repos.
- **Fuse the two sources.** One averaged or reconciled battery. Rejected for the reason above (ADR 0008); disagreement between a shunt and a BMS is information.
- **A kiosk-local or per-client primary battery.** Rejected above: it diverges between screens.
- **A dashboard-owned topic or setting.** The dashboard has no Pi-side process to persist state or republish it after a broker restart; it is a static web app.

## Rules this sets

- `driver`, when published, is the publishing Driver's key: equal to the `<driver>` segment of its `renewvan/<driver>/info` and `renewvan/<driver>/health`. The reserved key `demo` and a `driver-` prefix are rejected, exactly as for Driver info.
- `driver` and `name` are retained identity fields published together with the instance's other identity fields; neither is a live reading.
- Two Drivers must still not publish the same `renewvan/<entity>/<id>/...` (ADR 0008). `driver` says who owns an instance; it does not allow two owners of one id.
- Demo scenarios may name their Driver in `initial.driver`; `schema/validate-demo.py` then requires a fleet Driver that publishes that entity. The shipped demo has two battery scenarios, `house` (`venus`) and `wattcycle`, so Demo mode shows two sources for one bank.
- The hub plugin never validates `renewvan/hub/primary-battery/set` against instances, and a client never writes the state topic.

## Why this is an ADR

Hard to reverse: `driver` and `name` are new retained topics and schema properties that every Driver, the dashboard and the logger will rely on, and `renewvan/hub/primary-battery` is a public topic with a persisted value. Surprising without context: a reader will wonder why the owner is not derived from the info `publishes` join, why the instances' ids do not encode their source, why the primary battery lives on the hub plugin and not in the client that displays it, and why two instances of one bank are kept rather than merged. The result of a real trade-off: a required property or a fused battery would each be simpler to consume, at the cost of a coordinated release and of hiding a disagreement between two real instruments.

## See also

- `docs/adr/0008-node-naming-and-entity-neutrality.md`: one source per id; a shunt and a BMS on one bank are two views.
- `docs/adr/0011-node-manifest-and-retained-info-topic.md`: the `publishes` join and the per-instance attribution limit this supersedes.
- `docs/adr/0018-device-to-driver-rename.md`: the **Driver** vocabulary and the `driver-wattcycle` key change to `wattcycle`.
- `plugins/hub/README.md`, `schema/hub-primary-battery.schema.json`: the owner and the payload.
- `CONTEXT.md`: **Entity**, **Battery**, **Primary battery**, **Hub plugin**.
