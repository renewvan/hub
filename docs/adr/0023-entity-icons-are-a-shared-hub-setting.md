---
status: accepted
---

# Entity icons are a shared Hub plugin setting, not a property of the entity

The icon a client draws an entity instance with (a fridge probe as a refrigerator, the fresh tank as a droplet) is stored in one retained topic owned by the **Hub plugin**: `renewvan/hub/icons`, a JSON object whose keys are `<entity>/<id>` and whose values are lucide component names (`{"temperature/outdoor": "Park"}`). A client changes one entry by publishing `{"key": "<entity>/<id>", "icon": "Park"}` (or `"icon": null` to clear it), unretained, to `renewvan/hub/icons/set`; the plugin persists the map on the Pi and republishes it retained. Contracts: `schema/hub-icons.schema.json`, `schema/hub-icon-set.schema.json`. No entity schema, and no Driver, changes.

## Why

- **An icon is presentation, and `CONTEXT.md` already says so.** The **Relay** entry reads "grouping and icons are a consumer's presentation concern and never on the wire". Putting `icon` on `tank`, `temperature` and the next entity would have put a dashboard's vocabulary (lucide names) into every entity schema and made every Driver, including the ESP32 relay firmware, learn to store and echo it.
- **It must be shared.** The same reasoning as ADR 0019's **Primary battery**: the kiosk, a phone and a laptop must agree on what a sensor looks like, and a client-local choice (localStorage) diverges per client and is lost with a browser profile.
- **The Hub plugin is the existing owner of exactly this.** It already persists a hub-wide setting on the Pi and republishes it retained on every (re)connect, with the command-then-state shape (`.../set` unretained, the owner republishes retained). A new setting reuses that shape instead of inventing one, and one map covers every entity type with one change.
- **Keyed by entity and instance, not stored beside the entity.** The Hub plugin does not know which instances exist, which is why the key carries both the domain and the id (two entities may share an id, and ADR 0008 only forbids two owners of one `renewvan/<entity>/<id>`). It checks only the shape; a consumer ignores an entry whose instance is not on the bus.

## Rejected alternatives

- **An `icon` property on each entity, written by its Driver.** This was built first and reverted. It breaks the presentation rule above, needs a schema change and a Driver release per entity type, and ties every Driver to a UI vocabulary. It would also have had each Driver persist a value it has no use for.
- **The database that already exists (InfluxDB).** It is the logger's time-series history (one point per MQTT message, for Grafana). An icon is a current setting, not a series, and the dashboard has no query path to it: a static web app would need credentials and an API it does not have (ADR 0019 rejected a dashboard-owned setting for the same reason).
- **Browser-local (localStorage).** Rejected: it diverges between clients and is lost with the profile, the same objection ADR 0019 raised against a per-client primary battery.
- **A per-key topic (`renewvan/hub/icons/<entity>/<id>`).** Simpler payloads, but a client would need a wildcard subscription and could not tell the map is complete, and a stale entry could not be seen or pruned in one place.

## Rules this sets

- The state topic `renewvan/hub/icons` is written only by the Hub plugin; a client never publishes to it.
- A key is `<entity>/<id>` (lower-case entity domain, an id with no `/`, `+`, `#` or whitespace); a value is a lucide component name: PascalCase, letters and digits only, at most 64 characters. The plugin validates the shape and nothing else.
- A consumer must tolerate an entry for an instance that is not on the bus (ignore it) and a name it cannot draw (use its own icon). Clearing an entry (`icon: null`) restores the consumer's own icon.
- Demo mode (`renewvan/demo/...`) never writes to this map: nothing simulated is persisted (ADR 0010).
- Two clients setting the same key at once: the last command wins, as with the Primary battery.

## Why this is an ADR

Hard to reverse: `renewvan/hub/icons` and its `/set` topic are a public contract that clients and the persisted file depend on. Surprising without context: a reader will wonder why `name` and `driver` are properties of the entity while the icon is not, and why a Driver cannot set its own sensor's icon. The result of a real trade-off: putting it on the entity is the obvious shape and was built first, but it spreads a UI concern across every schema and Driver.

## See also

- `docs/adr/0019-entity-instances-name-their-driver-and-shared-primary-battery.md`: the shared-setting-on-the-Hub-plugin precedent this follows.
- `docs/adr/0013-writable-relay-and-its-wire-contract.md`: the relay's `name` is the only label on the wire.
- `docs/adr/0008-node-naming-and-entity-neutrality.md`: one source per `renewvan/<entity>/<id>`.
- `plugins/hub/README.md`, `schema/hub-icons.schema.json`, `schema/hub-icon-set.schema.json`: the owner and the payloads.
- `CONTEXT.md`: **Entity icons**, **Hub plugin**, **Relay**.
