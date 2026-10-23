---
status: accepted
---

# GPS per-satellite signal is one retained array, `satellites`

`gps.schema.json` gains an optional property, `renewvan/gps/<id>/satellites`: every satellite the receiver reports in view, as **one retained JSON array**. It exists so the dashboard's GPS settings view can draw a per-satellite signal chart (the view a GPS-status app shows), which `avg_snr_db` alone cannot: a mean hides "twelve satellites in view, three with signal".

Each item is `{ constellation, prn, snr_db, elevation_deg, azimuth_deg }`, all five required:

- `constellation`: `gps` / `glonass` / `galileo` / `beidou` / `qzss` / `other`, from the GSV talker ID. `other` is the escape hatch for a talker this schema has no name for, so a new constellation never invalidates the whole array.
- `prn`: the satellite number exactly as the receiver reports it. Unique only within a constellation; `(constellation, prn)` identifies a satellite.
- `snr_db`: C/N0 in dB-Hz, or `null`.
- `elevation_deg` / `azimuth_deg`: integers, or `null`.

The wire rules, which a consumer can rely on:

- **Optional and additive.** Existing `required` fields are unchanged. A driver older than this change publishes no `satellites`, and a consumer shows no chart.
- **Independent of fix status**, like `avg_snr_db`. A receiver tracking satellites with no fix still lists them; that is exactly the "satellites in view but not enough signal" case the chart is for.
- **Absent vs empty.** Absent only before the driver has decoded its first GSV sentence. `[]` means the receiver reported GSV with nothing in view.
- **Missing measurement is `null`, never `0`.** GSV leaves SNR (and sometimes elevation/azimuth) blank for a satellite the receiver knows of but is not yet measuring. `0` would read as a measured zero and drag the chart's floor; the same rule as ADR 0009's missing day.
- **Not `satellites_in_use`.** `satellites_in_use` is GGA's count of satellites used in the fix; `satellites` is what GSV lists in view. They are different populations and a consumer must not assume one is a subset of the other by count.
- **No `used` flag.** Marking which satellites went into the fix needs GSA sentences, which the driver does not read today. Not added speculatively; a later `used?: boolean` is additive.
- **Order is not significant.** The consumer sorts.
- **Length.** As many as the receiver lists, capped at 128 by the schema as a sanity bound (a multi-constellation receiver in open sky lists roughly 30 to 60).
- **Provenance.** Every value is the receiver's own report. The driver never computes or interpolates a satellite.

## Why

**A. One retained array (chosen).** A poll window decodes several GSV sentences per constellation; the set of satellites in view changes as a whole as the sky moves. One retained message makes that update atomic, so a consumer never draws a new satellite beside a stale one, and a satellite that sets simply disappears from the next array with no topic to clear. One schema property, one retained topic. The dashboard already copes: `useRenewvanBus.ts` runs `JSON.parse` on every payload and accumulates by `(id, property)`, so an array arrives as a value with no bus changes (ADR 0009 relied on the same fact).

**B. One topic per satellite (`satellites/<constellation>-<prn>/snr_db`...), ruled out.** It makes every entity topic four segments below the id, which breaks the `renewvan/<domain>/<id>/<property>` shape the dashboard, logger and demo namespace all parse. It also needs a retained-topic clear for every satellite that sets, and a consumer can observe a half-updated sky.

**C. Parallel arrays (`satellite_snr_db[]`, `satellite_prn[]`...), ruled out.** ADR 0009 suggests parallel arrays for per-day history, where the index is a stable day number. Satellites appear and disappear, so the indices stop meaning anything between polls and one dropped element silently misaligns every other array.

Consumers checked, not assumed:

- **logger**: `logger/mapping.py` logs only the properties in its per-domain `_FIELDS` table, and `topic_to_point` returns `None` for any other. It has no `gps` domain, so `satellites` is ignored and never written to InfluxDB. Intended: a per-satellite sky is a live diagnostic, not trip history.
- **driver-demo**: publishes `json.dumps(payload)` for any scenario value, so the array replays as is.
- **`plugins/`**: neither plugin reads `gps`.
- **Home Assistant MQTT discovery**: planned, not implemented. As with ADR 0009, it must publish this as a JSON attribute or skip it, not as a sensor state.

## Consequences

- `schema/gps.schema.json` adds `satellites`; `schema/examples/gps.*` and `schema/demo/gps.van.demo.json` exercise it (both a satellite with a measurement and one with `null`; a no-fix record that still lists satellites).
- `renewvan/driver-gps` keeps the per-satellite fields of every GSV sentence in a poll window (it already parsed them and kept only the SNRs) and publishes the array.
- The dashboard adds `satellites?: Satellite[]` to its `Gps` type and draws one bar per satellite in Settings › Devices › GPS.
- `avg_snr_db` stays, unchanged: it remains the one-number summary and the only signal field on a driver that predates this change.

## See also

- `docs/adr/0009-solar-yield-history-as-one-retained-array.md`: the precedent for a non-scalar retained property.
- `docs/adr/0008-node-naming-and-entity-neutrality.md`: why the constellation is a vendor-neutral word, not an NMEA talker ID.
- `CONTEXT.md`: `GPS` definition.
