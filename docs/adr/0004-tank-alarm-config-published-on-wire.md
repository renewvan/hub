---
status: accepted
---

# Tank alarm threshold/restore config is published on the wire, superseding ADR-0003

ADR-0003 kept `node-tank`'s per-tank `alarm_threshold`/`alarm_restore`/`alarm_direction` as local-only config, manually mirrored into `dashboard/src/lib/tank-alarm.ts`'s `TANK_ALARM_CONFIGS` for display zone coloring. We reversed that: `tank.schema.json` (v0.5) now carries `alarm_direction`, `alarm_threshold_pct`, `alarm_restore_pct` as retained, static wire fields, and the dashboard reads them from the live tank object instead of a hardcoded table.

## Why

ADR-0003 accepted the mirror's drift risk on the grounds that its blast radius was narrow (display-only zone coloring, never the real `alarm_state`) and that a drift-safety guard would reintroduce cross-repo build coupling ADR-0001 had already rejected. That tradeoff still holds on its own terms, but the maintainer decided the coordination cost of publishing the fields is worth paying to eliminate the hardcode entirely, rather than accept ongoing manual-sync risk indefinitely. This is a deliberate reversal of the prior call, not new evidence contradicting it — see ADR-0003 for the case against, which remains valid reasoning for a mirror-config approach in general.

Concretely: `alarm_direction`, `alarm_threshold_pct`, `alarm_restore_pct` are retained/static fields published once at startup in `node_tank/driver.py`'s `publish_identity()`, alongside the existing `fluid_type`/`capacity_l` identity fields — not per-tick like `level_pct`, since they're config, not live telemetry. They're gated on `tank.alarm_direction is not None`, the same condition that already gates `alarm_state` publication, so a tank with no alarm configured publishes none of the four alarm-related fields. This is additive: existing `required` fields are unchanged, so a dashboard running against an older node-tank build that doesn't publish these fields degrades to no alarm-zone coloring for that tank (the same behavior as today's "unknown id" fallback in `tank-alarm.ts`), rather than breaking.

## Why this is an ADR

Hard to reverse: it's the second reversal on this exact question inside one week (mirror → wire-field, having just reversed mirror-stays), and un-reversing again means another schema version, another 3-repo coordinated change, and another back-compat window — expensive to flip casually. Surprising without context: ADR-0003 sits right before this one in the same file, arguing the opposite conclusion with real reasoning; a reader needs this ADR to know which call is current and why. Result of a real trade-off: same tradeoff ADR-0003 weighed (coordination cost vs. mirror drift risk), landing on the other side of it this time.

## See also

- `docs/adr/0003-tank-alarm-config-stays-a-local-mirror.md` — the case against, still valid reasoning; this ADR reverses its conclusion, not its evidence.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md` — the cross-repo coupling cost this decision accepts paying.
- `CONTEXT.md` — `Tank` definition.
