---
status: accepted
---

# Relay groups are a shared Hub plugin setting, not a property of the relay or a dashboard file

Which group (Lights, Appliances, USB, ...) a client files a relay channel under is stored in one retained topic owned by the **Hub plugin**: `renewvan/hub/relay-groups`, a JSON object whose keys are relay ids and whose values are group names (`{"relay-x8-01": "Lights"}`). A client changes one entry by publishing `{"id": "<relay id>", "group": "Lights"}` (or `"group": null` to clear it), unretained, to `renewvan/hub/relay-groups/set`; the plugin persists the map on the Pi and republishes it retained. Contracts: `schema/hub-relay-groups.schema.json`, `schema/hub-relay-group-set.schema.json`. No relay schema, and no Driver, changes.

The list of groups a client offers (and its order) is a second setting of the same kind: a retained JSON array of group names on `renewvan/hub/relay-group-list`, replaced whole by publishing the same payload, unretained, to `renewvan/hub/relay-group-list/set` (`schema/hub-relay-group-list.schema.json`). It starts as the five stock groups (Lights, Appliances, USB, Water, Heating) and the owner edits it in Settings. The name `Unassigned` stands for the relays with no group: its place in the list is where they are drawn. A group has no icon: the Relays page draws groups as text.

The order of the relays inside a group card is a third setting of the same kind: one retained JSON array of relay ids on `renewvan/hub/relay-order`, replaced whole through `renewvan/hub/relay-order/set` (`schema/hub-relay-order.schema.json`), default `[]`. It is one array for every card, not one per group, so a relay keeps its place when it moves between groups and the plugin never has to know which relays are in which group; a card sorts its own relays by position in it, unlisted ones last.

## Why

- **A group is presentation, and ADR 0013 already says so.** Grouping and icons are a consumer's concern and never on the wire. A relay channel is an opaque latched output (ADR 0027): the ESP32 firmware cannot know it switches a light, and a name it stores is the only label it has.
- **It must be editable on the van.** The dashboard shipped with groups in a source file (`relayPanels.ts`), so moving a wire from a light to a fridge needed a code change and a dashboard release. The kiosk is a locked-down display: the owner changes the assignment in Settings, in the moment.
- **It must be shared.** The same reasoning as ADR 0019 and ADR 0023: the kiosk, a phone and a laptop must show the same panels, and a client-local choice (localStorage) diverges per client and is lost with a browser profile.
- **The Hub plugin is the existing owner of exactly this.** It already persists hub-wide settings on the Pi with the command-then-state shape (`.../set` unretained, the owner republishes retained). A new setting reuses that shape, the ACL rules (`hub` writes the state topic, `command` writes `.../set`) and the plugin's tests.

## Rejected alternatives

- **A `group` property on the relay, written by the relay Driver.** It puts a UI concept into the relay schema and makes every board's firmware store and echo it, for a value the firmware has no use for. The same objection ADR 0023 raised against an `icon` property.
- **The dashboard publishing the retained topic itself.** Simplest, but it needs a dashboard account with a write lock on a state topic, breaks the rule that a state topic has one writer, and would not survive a broker that lost retained state: nothing would republish it. The plugin does both.
- **Browser-local (localStorage).** Diverges between clients and is lost with the profile.
- **Keeping groups in `dashboard/src/config/relayPanels.ts`.** Needs a release per wiring change and cannot be shared with a client that is not this dashboard.
- **Per-relay topics (`renewvan/hub/relay-groups/<id>`).** A client would need a wildcard subscription and could not tell the map is complete, and a stale entry could not be seen or pruned in one place (ADR 0023).

## Rules this sets

- The state topic `renewvan/hub/relay-groups` is written only by the Hub plugin; a client never publishes to it.
- A key is a relay id (no `/`, `+`, `#` or whitespace); a value is a group name of 1 to 32 characters with no leading or trailing whitespace. The plugin validates the shape and nothing else.
- The list is up to 16 distinct names, in display order, and may be empty.
- `Unassigned` is a reserved name. A client never adds or renames a group to it; its entry in the list only records where the unassigned relays sit, and without one they are last.
- An assignment to a name that is not in the list is read as Unassigned. The plugin does not rewrite assignments when the list changes (it would make a rename a three-step dance against its own pruning), so the client that renames or removes a group first moves or clears its relays, and a stale entry is harmless.
- Until the list topic arrives (an older hub plugin never publishes it) a client falls back to the stock groups.
- A consumer must tolerate an entry for a relay that is not on the bus (ignore it). A relay with no entry is Unassigned, so a new channel always appears. Clearing an entry (`group: null`) returns it there.
- Demo mode (`renewvan/demo/...`) never writes to this map: nothing simulated is persisted (ADR 0010).
- Two clients setting the same relay at once: the last command wins, as with icons.

## Why this is an ADR

Hard to reverse: `renewvan/hub/relay-groups` and its `/set` topic are a public contract that clients and the persisted file depend on. Surprising without context: a reader will wonder why a relay's `name` is on the relay but its group is not. The result of a real trade-off: the first version kept groups in a dashboard source file, which is simplest and was shipped, and was replaced because the owner has to be able to regroup a van without a release.

## See also

- `docs/adr/0023-entity-icons-are-a-shared-hub-setting.md`: the shared-setting-on-the-Hub-plugin precedent this follows.
- `docs/adr/0013-writable-relay-and-its-wire-contract.md`: grouping and icons are never on the wire; the relay's `name` is the only label.
- `docs/adr/0027-per-board-drivers.md`: relay ids are opaque.
- `plugins/hub/README.md`, `schema/hub-relay-groups.schema.json`, `schema/hub-relay-group-set.schema.json`, `schema/hub-relay-group-list.schema.json`, `schema/hub-relay-order.schema.json`: the owner and the payloads.
- `CONTEXT.md`: **Relay groups**, **Hub plugin**, **Relay**.
