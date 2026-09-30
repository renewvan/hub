---
status: superseded by docs/adr/0004-tank-alarm-config-published-on-wire.md
---

# Tank alarm threshold/restore config stays a local mirror, not a wire field

`node-tank/config.default.ini` carries each tank's `alarm_direction`, `alarm_threshold`, `alarm_restore`, and `alarm_delay_s`. `dashboard/src/lib/tank-alarm.ts` hand-mirrors the threshold/restore numbers into `TANK_ALARM_CONFIGS` so the dashboard can render caution/danger zone coloring without waiting on a wire-carried `alarm_state` transition. We considered publishing these as new retained fields on `tank.schema.json` (e.g. `alarm_threshold_pct`/`alarm_restore_pct`) so the dashboard reads them live off the tank object instead. We decided to keep the manual mirror.

## Why

Threshold/restore/direction/delay are operator-tunable *config*, not sensor-reported *telemetry* — the same class of value this project already keeps off the wire. `alarm_direction` and `alarm_delay_s` are parsed into `node_tank`'s `TankConfig` dataclass and never appear in any published MQTT payload; `tank.schema.json`'s `alarm_state` field description ("present only if alarm_direction is configured") already documents a wire-visible *presence* effect of local config, not the config value itself. Publishing threshold/restore on the wire would introduce a new category of wire field — device config, not device state — where none currently exists, breaking with established convention rather than extending it.

Wire-field's cost is real and immediate: a schema version bump, coordinated changes across `node-tank` (`publisher.py`/`driver.py`) and `dashboard` (`types.ts`, `tank-alarm.ts`, `TankCard.tsx`), and a back-compat story for dashboards running against node-tank versions that predate the new fields. `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md` already rejected build-time coupling between these repos once, for the same reason: it re-couples independently-released services' timing.

The mirror's known failure mode — `dashboard/src/config/relayLabels.ts` has already silently drifted from `node-relay/relay-node.yaml`'s real channel ids, uncaught by any test or CI — was weighed and accepted rather than guarded against. We considered adding a drift-safety check (a test reading `config.default.ini` directly, or a shared JSON/YAML source both repos load) but rejected both: either requires new cross-repo build/CI coupling of exactly the kind ADR-0001 avoided, to guard against a small blast radius. Unlike relayLabels (a mislabeled or unlabeled physical relay), a stale mirror value here only shifts *display* zone coloring — the dashboard's local caution/danger threshold for its own UI. It never touches the actual `alarm_state` the tank publishes, which node-tank computes and gates entirely on its own local config; a stale dashboard mirror cannot mask or fabricate a real alarm condition. The existing header-comment discipline in `tank-alarm.ts` ("keep in sync with the node's config when a tank's alarm values change") is proportionate to that blast radius.

## Why this is an ADR

Hard to reverse in practice: once the dashboard ships a UI built against a hardcoded mirror, moving to wire-fields later means a schema bump, a 3-repo coordinated change, and a back-compat window — not a quick reversal. Surprising without context: a reader who sees `hub` owns the shared schema and a threshold changing across two repos might expect that value to be schema-carried; this ADR records that it deliberately isn't, and why. Result of a real trade-off: both paths were genuinely viable, and the alternative (wire-field) was worked out in enough detail (field names, retained/static semantics, back-compat) before being rejected.

## See also

- `.scratch/tank-alarm-config/issues/01-mirror-vs-wire-field-decision.md` — full discussion and evidence.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md` — the earlier decision against cross-repo build/CI coupling this ADR extends to config drift-guarding.
- `CONTEXT.md` — `Tank`, `Node` definitions.
</content>
