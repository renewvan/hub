---
status: accepted
---

# Removed Drivers are cleared from the bus, and the hub publishes the fleet with a contract version

Driver info, health and entity topics stay retained when a Driver goes offline ([ADR 0011](0011-node-manifest-and-retained-info-topic.md)), so a Driver that was uninstalled for good looked exactly like an offline one: its info stayed, its health said `offline`, and its widgets stayed on every client's screen for ever. ADR 0011 called cleaning that up "out of scope"; this ADR is the scope, requested by the apps in [renewvan/hub#203](https://github.com/renewvan/hub/issues/203) so their widgets and Settings can follow the installed Drivers with no app release per Driver.

This decides four things:

1. **Removal is an operator action, `bin/remove_driver.py`.** Run on the Pi after the Driver is stopped and uninstalled, it reads what is actually retained and clears it: the Driver's whole namespace (`renewvan/<key>/info`, `health`, `winter-mode`, …) and every entity instance that names the Driver in its retained `driver` property ([ADR 0019](0019-entity-instances-name-their-driver-and-shared-primary-battery.md)). It refuses while the Driver's health is `online` (a live Driver would republish at once) unless `--force`, warns when the Driver is the Hub power board (`hub_power` in its info, [ADR 0033](0033-winter-mode-and-hub-power.md)), lists instances it cannot attribute, and verifies nothing is left. It generalises `driver-esp32/scripts/clear_board.py`, which until now was the only removal tool and only covered relay boards.
2. **The hub publishes the fleet.** The hub plugin maintains retained `renewvan/hub/fleet` (`schema/hub-fleet.schema.json`): `{"contract": 1, "drivers": [...]}`, the sorted keys of every Driver with retained info. It subscribes to `renewvan/+/info`, rebuilds the set from the retained replay on every (re)connect, drops a Driver the moment its info is cleared (an empty retained payload is the removal signal), and republishes on every change. The first publish after a connect waits a short settle window for the replay to arrive: publishing the just-reset set at once would overwrite a good retained fleet with an empty one in that window, and a consumer connecting then would settle on "no Drivers installed" — the exact false answer the topic exists to kill. A consumer settles once it has seen the retained info of every Driver the fleet names — replacing the fixed settle timer the apps ran after each connect — with a timeout as the fallback for a hub plugin that is down or older than this topic.
3. **The bus contract is versioned.** `contract` in the fleet payload is one integer for the whole contract: the entity schemas, the Driver info/health conventions and the hub topics. It starts at 1 and is bumped by a hub release when the contract changes in a way a consumer must know about; a consumer built for a lower number than it sees knows it is behind.
4. **`driver` stays optional on entity instances.** The issue asked us to consider requiring it so every instance can be attributed. ADR 0019 already rejected that: it would invalidate every Driver that has not shipped it and every retained instance on a broker, forcing one coordinated release across all Driver repos. Instead the removal tool attributes by the `driver` property every in-repo Driver already publishes, and lists the instances of a published type that carry none (legacy or third-party) so the operator can clear them by hand rather than have the tool guess an owner.

## Why

- **A broker cannot say "removed".** MQTT retained state has no delete event a late subscriber can see: clearing a topic is indistinguishable from it never having existed, and nothing notifies a client that connects later. So "gone" must be an affirmative record, not an absence. The fleet is that record: a Driver in the fleet with health `offline` is offline; a Driver absent from the fleet is not installed. That is the signal the apps needed to keep "offline = still visible" without inventing an age timer, which the issue explicitly ruled out.
- **Removal belongs on the Pi, not in the protocol.** A "please remove me" topic a Driver publishes before dying cannot cover the real cases (the Driver is already broken, wiped, or its board re-flashed), and a broker-side expiry would fight ADR 0011's "offline, last seen vX". An operator tool run after uninstall matches how Drivers are actually removed: `docker compose` edit on the Pi, or a board powered off for good.
- **A fleet list beats a replay-complete marker.** A retained "snapshot complete" flag cannot work: the broker replays retained messages per subscriber connect, with no end signal, and one flag cannot be sequenced after each subscriber's replay. The list of what _should_ replay is the workable form of the same idea — the consumer knows what to wait for — and it doubles as the removal signal and the unknown-Driver detector (an entity whose `driver` names no fleet member is visible as unattributable).
- **`contract` versions the contract, not the software.** Driver info `version` is the Driver's release and hub status `version` is the hub deploy; neither tells a consumer whether the bus speaks a newer contract than it was built for. One integer the hub stamps is enough for an old app to say "this van is ahead of me".
- **Rebuilding from replay keeps the plugin dumb.** The fleet needs no persistence: every Driver republishes its info on every connect (ADR 0011), and the plugin rebuilds on every connect of its own, so a broker wipe, a plugin restart and a removal while the plugin was down all heal by the same path that heals everything else.

## Rules this sets

- A Driver is removed by clearing its retained topics with `bin/remove_driver.py` after it is stopped and uninstalled — never by hand-editing retained state, and never while it is `online` without `--force`. The ESP32-only `clear_board.py` keeps its board-specific validation; anything else uses this tool.
- An empty retained payload on `renewvan/<driver>/info` is the removal signal. The hub plugin drops the Driver from the fleet on it, and a consumer must treat a Driver absent from the fleet as not installed.
- `renewvan/hub/fleet` is hub-published and retained; `drivers` is sorted and carries Driver keys only (the reserved keys `demo` and `hub` never appear). A consumer settles after seeing the info of every listed Driver, with a timeout fallback; it must tolerate the topic being absent entirely (a hub plugin older than this ADR).
- `contract` starts at 1 and only moves up, in a hub release that says why in its notes. A contract change that old consumers cannot ignore is exactly what the number is for.
- Making `driver` required on every entity stays rejected (ADR 0019). Removal attributes by the optional property; unattributable instances are reported, never cleared by guesswork.
- A Driver must publish retained info before its entities and before `online` — already the authoring rule (`docs/authoring-a-driver.md`); the fleet now makes a Driver that skips it visible as a fleet of fewer members than its entities imply, so no new enforcement is added.

## Rejected alternatives

- **A last-seen timestamp in health.** The issue offered it as the alternative to removal. Rejected as the primary mechanism: health is a bare retained word maintained by the last will ([ADR 0011](0011-node-manifest-and-retained-info-topic.md)), and the broker cannot stamp a disconnect time into a will payload, so every Driver repo would have to publish heartbeats — a coordinated change across all repos to produce a weaker signal (a stale timestamp still needs an app-side age rule, which the issue ruled out). The fleet's presence/absence is exact and needs no Driver change.
- **App-side age rule (offline longer than N days hides the Driver).** The issue ruled it out and we agree: it guesses, it hides a Driver that is legitimately off for a season, and it hides the difference between "gone" and "offline" that removal exists to make explicit.
- **Making `driver` required on every entity.** Rejected in ADR 0019 for the coordinated-release cost; reaffirmed above.
- **A retained "snapshot complete" marker.** No per-subscriber replay end exists to mark; the fleet list is the workable form of the same idea.
- **Broker-side expiry of stale retained topics.** A broker plugin that clears topics unseen for N days fights "offline, last seen vX" and deletes state during long offline periods (winter storage). Removal is an operator decision, not a timer.
- **Persisting the fleet on the Pi.** One more file to disagree with the bus. Rebuilding from the retained replay on every connect is simpler and self-healing.
- **Including demo Drivers in the fleet.** Their infos sit at `renewvan/demo/<driver>/info`, one segment deeper, and driver-demo already knows its fleet from the hub-owned fleet file ([ADR 0010](0010-demo-mode-publishes-under-renewvan-demo.md)); the demo validator owns that side.

## Why this is an ADR

Hard to reverse: `renewvan/hub/fleet`, the removal semantics of an empty retained info payload and the `contract` number are a public wire contract that the apps, future consumers and `bin/remove_driver.py` all depend on by name. Surprising without context: a reader will wonder why removal is a Pi-side operator tool rather than a wire command, why the fleet is rebuilt from replay instead of persisted, why a "snapshot complete" flag does not exist, and why `driver` is still optional after all this. The result of a real trade-off: a last-seen timestamp or a required `driver` would each look tidier, at the cost of a coordinated release across every Driver repo for a weaker signal.

## See also

- [renewvan/hub#203](https://github.com/renewvan/hub/issues/203) — the apps' request this answers.
- [ADR 0011](0011-node-manifest-and-retained-info-topic.md) — retained info and health, whose "cleanup out of scope" this supersedes.
- [ADR 0019](0019-entity-instances-name-their-driver-and-shared-primary-battery.md) — the optional `driver` property removal attributes by, and why it stays optional.
- [ADR 0033](0033-winter-mode-and-hub-power.md) — the Hub power board the removal tool warns about, and its `winter-mode` topic the tool clears with the namespace.
- `docs/authoring-a-driver.md` — the publish-order rule the fleet relies on.
- `schema/hub-fleet.schema.json` — the fleet payload contract.
