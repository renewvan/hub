---
status: accepted
---

# Remove `fill_rate_lpm`/`drain_rate_lpm`

`tank.schema.json` (v0.3) added `fill_rate_lpm`/`drain_rate_lpm`: node-tank's instantaneous, edge-to-edge liters-per-minute flow rate, published unconditionally on every read. We removed them (v0.7, breaking): the fields, their computation (`_compute_flow_rates`/`FlowState`) and tuning config (`flow_min_delta_pct`/`flow_idle_timeout_s`) in `node-tank`, and their type in `dashboard` are all gone.

## Why

Auditing the dashboard's tank card while fixing its mislabeled "Fill Rate"/"Drain Rate" telemetry (they displayed `volume_since_full_l`/`volume_since_empty_l`, cumulative volumes, under rate-sounding labels with no unit-per-time) surfaced that `fill_rate_lpm`/`drain_rate_lpm` — the schema's actual instantaneous rate fields — had no consumer at all, anywhere in the dashboard, ever. The card's redesign settled on a pace-based metric instead (liters/hour averaged since the last full/empty latch), computed dashboard-side from `volume_since_full_l`/`volume_since_empty_l` and `last_full_at`/`last_empty_at` — all already on the wire. That leaves nothing reading `fill_rate_lpm`/`drain_rate_lpm`: an instantaneous L/min number is only meaningful while a pump is literally mid-flow, a narrower and less useful signal than the pace-since-refill number the card now shows. Keeping node-tank compute a value nothing reads, and keeping a schema field publishing it, is unjustified maintenance surface.

Unlike `docs/adr/0005-remove-level-pct-smoothed.md` (which explicitly kept this exact `_compute_flow_rates`/`FlowState` machinery alive for these two fields), this ADR removes that machinery outright — it was the fields' only reason to exist, and both are now gone.

## Why this is an ADR

Hard to reverse: schema v0.3 made these required fields; removing them is a breaking wire change any lagging consumer would need to handle, and re-adding them later means redoing the edge-tracking rate logic node-tank had already built and tested. Surprising without context: `docs/adr/0005` explicitly argued for _keeping_ this machinery on the grounds these fields had "their own consumers" — a reader needs this ADR to know that assumption turned out false and the field was removed anyway. Result of a real trade-off: the fields weren't broken, they were confirmed unused and traded away for a leaner schema.

## See also

- `docs/adr/0005-remove-level-pct-smoothed.md` — the prior schema version bump, same file's version-history convention; explicitly kept this machinery alive on the (mistaken) assumption these fields had consumers.
- `CONTEXT.md` — `Tank` definition.
