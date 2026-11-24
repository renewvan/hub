# Stepped tank levels: lower-edge `level_pct` plus `level_marks_pct`

A tank can be read by **Level probes** (conductive rods or screws at fixed heights, e.g. the CBE 4-rod fresh probe or a single pair of screw probes at a grey tank's full mark) instead of a continuous Sender. A probe only says wet or dry, so the level is known in steps. We keep `level_pct` required and publish it as a **Stepped level**: the **Level mark** of the highest wet probe, or 0 when all are dry. A new optional, retained `level_marks_pct` (the marks as volume %, ascending) is present only on such a tank and tells consumers the level is stepped. On a stepped tank `volume_since_full_l`/`volume_since_empty_l` are always 0, and the full/empty latches commit only when the band is certain (full when a probe at or above the full threshold is wet; empty only if a probe sits at or below the empty threshold).

## Considered options

- **Make `level_pct` optional and add a band field** (e.g. `level_band` or per-probe booleans). Rejected: every consumer would have to handle a tank without `level_pct`, and the alarm, latch and logging paths all key off it.
- **Publish the band midpoint** (17/50/83 for marks 33/66/100). Rejected: it invents precision. A lower bound is honest and makes a high alarm trip only when water is truly at the mark.
- **Publish the lower edge with no marker.** Rejected: the dashboard would draw "0 %" or "66 %" on a continuous gauge with no way to say "at least 66 %".
- **Derive `volume_since_*` from the steps.** Rejected: one probe at 100 would report 70 L drained the moment the level dipped below the mark.

## Consequences

- A tank with water below its lowest probe reads `level_pct` 0. Consumers that see `level_marks_pct` should render marks ("≥ 66 %") rather than a gauge, and hide `volume_since_*`.
- `level_marks_pct` is volume %, after the tank's shape correction, so `level_pct` and the marks are comparable.
- A probe pattern that is physically impossible (a wet probe above a dry one) is not yet a `status` value; it is logged by the Driver. A dedicated status would be a later enum addition.
