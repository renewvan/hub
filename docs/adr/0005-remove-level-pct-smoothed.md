---
status: accepted
---

# Remove `level_pct_smoothed`

`tank.schema.json` (v0.4) added `level_pct_smoothed`: a rate-extrapolated display value node-tank computed for stepped/reed-switch senders, so the dashboard could show a smoothly moving fill percentage between a sender's discrete resistance steps instead of `level_pct`'s real, occasionally jumpy readings. We removed it (v0.6, breaking): the field, its computation in `node-tank`, and its type in `dashboard` are all gone. `TankCard` now shows the raw `level_pct` directly, as does every other consumer already did.

## Why

The maintainer decided `level_pct` — the sender's actual reading — is the more reliable number to show the driver, even on a stepped sender where it occasionally jumps rather than sweeping smoothly; a fabricated smoothed value between real readings is a cosmetic nicety, not a reliability win, and the driver-facing display should show what the sensor actually reported. `TankCard` (the only consumer that ever read `level_pct_smoothed`) already switched to `level_pct` for its fill height/percentage/liters; `TankRowCard`, `TankStatCard`, and `TanksTab`'s prototype variants read `level_pct` directly and never consumed the smoothed field at all. With no remaining consumer, keeping node-tank compute a value nothing reads, and keeping a schema field publishing it, is dead weight — this repo's convention is a clean cutover, not a speculative field kept alive for a hypothetical future consumer.

The underlying `_compute_flow_rates`/`FlowState` machinery (edge tracking, fill/drain rate) stays: it's still required for `fill_rate_lpm`/`drain_rate_lpm`, which are unrelated live fields with their own consumers. Only `_smoothed_level_pct` — the function built on top of that state purely to produce the now-unused display value — and its publish call are removed.

## Why this is an ADR

Hard to reverse: schema v0.4 made this a required field; removing it is a breaking wire change any lagging consumer (a node-tank or dashboard build still on the old contract) would need to handle, and re-adding it later means redoing the extrapolation logic node-tank had already built and tested. Surprising without context: a reader of `docs/adr/`'s trail (0003 mirror → 0004 wire-field) would reasonably assume schema fields only ever get added, not removed; this records the one field that went the other way and why. Result of a real trade-off: the field wasn't broken or wrong, it was deliberately traded away for showing the driver a more literal (if occasionally jumpier) number.

## See also

- `docs/adr/0004-tank-alarm-config-published-on-wire.md` — the most recent prior schema version bump, same file's version-history convention.
- `CONTEXT.md` — `Tank` definition.
</content>
