---
status: accepted
---

# The relay is writable, with a safety-first wire contract

The **Relay** entity changes from read-only to read-write. A channel publishes a retained `state` and an optional retained `name`, and accepts two unretained commands. The contract is `schema/relay.schema.json`; this ADR records the decisions behind it.

**The broker accepts anonymous clients, so after this change anyone who can reach port 1883 or the 9001 WebSocket listener can switch a load, including a water pump or a heater.** `docker/mosquitto/config/mosquitto.conf` sets `allow_anonymous true`. That was acceptable while every entity was read-only; it is not once a relay is writable. This ADR does not fix it. A separate, hub-wide effort (broker authentication and ACLs) must lock the broker down before a relay controls a pump on any untrusted network. Until then the broker must only be reachable from the van's own network.

## The contract

- **Ids.** Positional and zero-padded: `relay_01` to `relay_16` on the 16-channel board. No schema constrains an id, and the dashboard has no relay-id assumption.
- **`state`.** A retained JSON boolean, published only _after_ the physical output changed, so it is the Node's truth and not an echo of a command. It means "commanded and latched", not "the load is energised": with no feedback sensor a stuck relay or blown fuse is not detected, and no UI may claim otherwise. A consumer shows "pending" until it arrives.
- **`name`.** An optional, retained, non-empty JSON string: the only label on the wire. On the ESP32 Node its limit is 63 bytes by default (254 to 255 at most), counted in bytes, so non-ASCII names are shorter in characters. Optional because a source with no label (a later Pi-wired Node) must still validate (ADR 0008: required only what every source can supply).
- **`renewvan/relay/<id>/set`.** Unretained, JSON `true` or `false` only. There is no toggle: a toggle is not idempotent, and a duplicated or dropped message could flip a pump the wrong way. The consumer sends the target state.
- **`renewvan/relay/<id>/name/set`.** Unretained, a JSON string. It has five segments, so it can never match the four-segment entity pattern in the dashboard or the logger.
- **Unknown.** A consumer treats `state` as unknown whenever the owning Node's health is not `online` or is absent (ADR 0011: a Node with info but no health is neither online nor offline). No field is added for this: an MQTT last will carries one topic, so a Node cannot clear sixteen retained state topics from its will.
- **No reserved ids.** `info` and `health` are three segments and entity topics are four, so the dashboard's and logger's segment-count filters already keep them apart. A relay may be called `health`; nobody should add a rule expecting a rejection.
- **Removing an id.** A Node that stops publishing an id it still knows about publishes an empty retained payload (ADR 0008's "no value"). A Node cannot discover ids it no longer lists, so one-time cleanup of old ids belongs to the cutover.
- **One source per id.** Two Nodes must not publish the same `renewvan/relay/<id>/...`.
- **Documentation.** `set` and `name/set` are described in the entity's schema description and the Node's README, not as separate schema files, as temperature's `name/set` and tilt's `level_zero/set` are. The one separate command schema (the kiosk display power) is for a host plugin, not an entity.
- **Groups and icons.** Dashboard configuration keyed by channel id, never on the wire.

## Boot and failure safety

A reboot, brownout, update or broker outage that flips a load is a real fault on this board, not a display glitch. The Node alone owns boot state.

- **Every channel is off after any restart** (reset, brownout, update, crash). No relay state is restored, so a relay turned off before a crash stays off, and one turned on never returns on its own. The hub never re-asserts a desired state.
- **Relays hold their last state while WiFi or the broker is down.** There is no firmware timeout-to-off and no local control; a manual override is a hardware concern.
- **A clean MQTT session**, so a `set` published while the Node was offline is not queued and replayed at reconnect. Whether Mosquitto would queue a QoS 0 message for an offline persistent session is unverified; the requirement stays because it costs one key.
- **Publish order on every connect, in one chain:** all sixteen retained states, then the retained Node info, then retained `online`. A consumer that sees `online` never finds a stale "on" for a load that is off, and info stays immediately before `online`, so ADR 0011 holds as written.
- **No native API block** (removes ESPHome's fifteen-minute no-client reboot) and **the web server disabled** (an unauthenticated second control path that would bypass this contract).
- **An external pull-up on the shift register's output-enable pin** keeps the outputs high-impedance until firmware drives it. ESPHome drives nothing before setup, so a boot glitch exists whatever the restore mode; fitting the pull-up is an acceptance step, and its polarity depends on the board.
- **Interlocks live in hardware**, never solely in software: a pump dry-run cutoff, a heater thermal cutoff. The Node and the bus do no interlock logic.

## Why

- **Idempotent commands over a toggle.** On an unreliable network a repeated or lost command must converge, not oscillate.
- **`state` after the output, not after the command.** If `state` echoed the command, a failed write would still read as success. A consumer that waits for `state` can report a command that did not take effect.
- **Boot state is the Node's alone.** A hub that re-asserted desired state after a reboot would make a stale "on" a live fault, and would need to know which loads are safe to restore.
- **The rename is its own topic.** Keeping `name` out of `set` keeps the command payload a bare boolean.

## Rejected alternatives

- **A toggle command.** Smaller payload. Rejected: not idempotent.
- **Restoring the last state at boot.** A relay would survive a reset unchanged. Rejected: a stale "restore last state" can switch a pump back on after a crash.
- **A label file in the consumer.** No name on the wire. Rejected: labels drift from what the board publishes; `name` is the only label.
- **Per-relay health or an `unknown` state field.** Rejected: one Node health word already says whether any of its states can be trusted, and a will cannot clear sixteen topics.
- **ESPHome's native command topic.** Rejected: it speaks only `ON`/`OFF`/`TOGGLE`.

## Why this is an ADR

Hard to reverse: the command topics, payload shapes and the meaning of `state` are a public wire contract that the Node, the dashboard and any future relay Node depend on. Surprising without context: a reader will wonder why there is no toggle, why no state is restored at boot, why `state` does not mean the load is on, and why the broker is left anonymous after a writable entity shipped. The result of a real trade-off: a toggle and a restored state are simpler, at the cost of a pump that can flip on a lost message or a crash.

## See also

- `docs/adr/0008-node-naming-and-entity-neutrality.md` — required properties only what every source can supply; the empty retained payload as "no value".
- `docs/adr/0011-node-manifest-and-retained-info-topic.md` — `info` before `online`, and a Node with info but no health.
- `docs/adr/0012-board-named-relay-node.md` — why the ESP32 Node is `node-esp32-relay`.
- `docs/adr/0010-demo-mode-publishes-under-renewvan-demo.md` — Demo mode never publishes a relay command; the demo ids overlap the real ids.
- `schema/relay.schema.json` — the machine-checked contract.
