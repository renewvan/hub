---
status: accepted
---

# Solar daily yield history is one retained array, `yield_history_wh`

`solar.schema.json` gains an optional property, `renewvan/solar/<id>/yield_history_wh`: the controller's own daily yield log as **one retained JSON array**, in watt-hours, newest first. Index 0 is the controller's current day (so it equals `yield_today_wh`), index 1 the day before, and so on. It exists so the dashboard's Power page can draw the same day-by-day bar chart Venus shows in its Solar tile.

The wire rules, which a consumer can rely on:

- **Optional and additive.** Existing `required` fields are unchanged. A controller with no log (a cheap PWM one), or an older `node-venus`, publishes no `yield_history_wh`, and a consumer shows no chart.
- **Length.** As long as the controller has days logged, never padded to a fixed size. A new controller publishes a short array. The schema caps it at 366 entries as a sanity bound; Venus's MPPT logs 31 days.
- **Missing day.** `null`, never `0`. `0` is a real day that harvested nothing (a Victron MPPT logs these: the capture below has 14 of its 31 days at 0), so a gap and an empty bar are different things and a consumer must be able to tell them apart.
- **Day boundary.** Index 0 is whatever day the controller is currently accumulating, the same boundary `yield_today_wh` already documents ("local midnight, or the controller's own day boundary"). On Venus, slot 0 is exactly the value `yield_today_wh` is mapped from, so the two cannot disagree.
- **Provenance.** Every value is the controller's own log. A node never computes, interpolates or backfills history.

## What the controller publishes (captured, not assumed)

Captured on 2026-10-15 from a live GX (Venus OS v3.80, one MPPT, device instance 291). The captured payloads are `node-venus/tests/fixtures/victron.py` (`SOLAR_YIELD_HISTORY_KWH`, `SOLAR_DAYS_AVAILABLE`).

- Venus publishes `N/<portal>/solarcharger/<n>/History/Daily/<0..30>/Yield` in **kWh**, slot 0 = today, **31 slots**, and `History/Overall/DaysAvailable` (31). Per slot it also publishes `MaxPower`, `MaxPvVoltage`, `MinBatteryVoltage`, `MaxBatteryVoltage`, `MaxBatteryCurrent`, `TimeInBulk`/`TimeInAbsorption`/`TimeInFloat` (minutes), `Nr` (a day counter), `LastError1..4` and `Consumption`. The paths and units match what the issue expected; slot 0 matches the issue's example (Yield 0.21 kWh, 52.60 V, 136 W, Bulk 637 min = 10:37 hr).
- **A fresh subscribe delivers every slot at once.** They are not broker-retained (a subscriber that asks not to receive retained messages still gets them), the node's Venus-side subscription is a plain `N/<portal>/#`.
- **The keepalive makes Venus send everything again.** An already-subscribed client received the same 473 `History/#` messages a second time after one `R/<portal>/keepalive` (473 then 946). `node-venus` sends one every 30 s, so history is re-sent every 30 s. The "Venus only re-sends on change" behaviour `subscriber.py` documents therefore does not stop a freshly started node learning the older days; it learns all of them within one subscribe.
- **Rollover (inferred, not observed).** Venus's MPPT driver is not readable Python on the device, and the capture was taken after sunset, so no rollover happened during it. What the data shows is that slots are positional (slot _n_ is the _n_-th most recent day) and `Nr` is a counter that falls by one per slot, so a new day must shift every slot by one place and re-send each. That is the case the shape has to survive; it is not a measured one.

## Why

Two shapes were weighed.

**A. One retained array (chosen).** For: a day rollover shifts every slot, and one retained message makes that atomic, so a consumer never reads a new slot 0 beside an old slot 1. It is a one-property schema diff, one retained topic per controller, and the dashboard already copes: `useRenewvanBus.ts` runs `JSON.parse` on every payload and accumulates by `(id, property)`, so an array arrives as a value with no bus changes. Against: it is the first non-scalar property on the bus.

**B. One scalar property per day (`yield_day_0_wh` .. `yield_day_30_wh`), ruled out.** It fits the existing stateless mapper unchanged, but costs 31 near-identical schema properties, 31 retained topics per controller, and a non-atomic rollover that a consumer can observe half-done. The only thing in its favour was avoiding a stateful mapper, and the stateful part is small (below).

The non-scalar was checked against the consumers that read the bus, not assumed:

- **logger**: `logger/mapping.py` logs only the properties in its per-domain `_FIELDS` table, and `topic_to_point` returns `None` for any other property. `yield_history_wh` is not in the table, so it is ignored. It is not written to InfluxDB, which is intended: the controller already keeps the log, and `yield_today_wh` is what gets recorded.
- **`plugins/`**: neither plugin reads `solar`.
- **Home Assistant MQTT discovery**: planned in `docs/architecture.md`, not implemented. It will need to publish this property as a JSON attribute or skip it, not as a sensor state, because an array is not a valid state value. That is a constraint on that future work, noted here so it is not rediscovered.

### The accumulator and the burst

Option A means `node-venus` must fold Venus's one-message-per-slot feed into one array, the first state in an otherwise stateless mapper (`SolarYieldHistory` in `mapping.py`, still pure: no network, no clock). The first version published as slots arrived, and measured against the captured data a single rollover burst produced **18 intermediate arrays**, each a mix of old and new slots. That is exactly the non-atomic state Option A was chosen to avoid, so the design does not publish from the message handler. The subscriber publishes once the burst of history messages has been quiet for two seconds, and only when:

- the array is complete: every slot `DaysAvailable` promises has been received _since the last publish_, not merely at some point. A node that has just started never publishes a half-filled history, and a burst that stalls part way (a slow link to the GX) waits for the rest instead of publishing its new slots beside the previous burst's old ones, which is the half-shifted array again. A review of the first version found exactly that gap; and
- it differs from the last one published, so the keepalive re-send does not republish every 30 s.

A controller reporting `DaysAvailable` of 0 publishes nothing: no days logged means no history, which is the property being absent (no chart), not an empty array (a chart with no bars). A negative yield is not a reading the schema allows, so it is published as `null`. A Venus `null` slot becomes a `null` entry in the array and the array still publishes. This differs from the scalar mapping, where a `null` clears the retained topic: clearing the whole history because one day is unknown would discard the other 30 days. If a controller stops reporting its days entirely, the last array stays retained; `node-venus` does not clear it.

## Why this is an ADR

Hard to reverse: it fixes the property name, ordering, units and the `null`-means-gap encoding that the dashboard, and any later node (`node-vedirect`, a Renogy or Epever node) must match, and switching from one array to per-day properties later means a schema change, a cross-repo migration and a back-compat window. Surprising without context: this is the first non-scalar on the bus, and a reader who knows every other property is a scalar will assume it is a mistake. Result of a real trade-off: atomic rollover and one topic against a stateful mapper and a first non-scalar, with a measured reason (18 half-shifted arrays) for the debounce that the obvious implementation lacks.

## Follow-ups, not part of this decision

- The other per-day fields Venus logs (`MaxPower`, `MaxPvVoltage`, the battery voltages and current, `TimeInBulk`/`TimeInAbsorption`/`TimeInFloat`) are deliberately not on the wire: nothing reads them, and ADRs 0005 and 0006 removed fields nobody read. If the dashboard grows a history table like Venus's, propose them as further parallel arrays (same indexing as `yield_history_wh`) rather than an array of objects.
- The dashboard adds `yield_history_wh?: (number | null)[]` to its `Solar` type and draws one bar per day, leaving a gap for `null`.

## See also

- `docs/adr/0004-tank-alarm-config-published-on-wire.md` — the additive, optional-property stance this follows.
- `docs/adr/0008-node-naming-and-entity-neutrality.md` — why the name is `yield_history_wh`, not Victron's `History/Daily`.
- `docs/adr/0005-remove-level-pct-smoothed.md`, `docs/adr/0006-remove-fill-rate-drain-rate-lpm.md` — why the other History fields are not added.
- `CONTEXT.md` — `Solar` definition.
