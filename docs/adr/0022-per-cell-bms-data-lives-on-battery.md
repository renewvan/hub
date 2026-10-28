---
status: accepted
---

# Per-cell BMS data lives on `battery`, not a separate `bms` entity

`battery.schema.json` gains optional properties that carry what a BMS knows about its pack, published by the same instance that already carries `soc_pct`, `voltage_v`, `current_a` and `power_w`. No new entity and no new Driver: `driver-wattcycle` (JBD/DH04 over BLE) and `driver-venus` (Venus OS MQTT) publish them on their existing `battery` instances. This reverses the line in `CONTEXT.md` and ADR 0019 that gave a BMS's per-cell data its own future `bms` entity.

The properties (all optional, all retained):

- `cell_voltages_v`: array of volts, one per cell, index _i_ = cell _i_+1. `minItems 1`, `maxItems 32`, items `number|null`, `minimum 0`.
- `cell_voltage_max_v`, `cell_voltage_min_v`, `cell_voltage_delta_v`: scalars, `minimum 0`.
- `cell_balancing`: boolean array index-aligned with `cell_voltages_v` (`null` = unknown, never `false`), `minItems 1`, `maxItems 32`.
- `temperatures_c`: per-probe array, `null` for an absent or unreadable probe, `minItems 1`, `maxItems 16`. Independent of the cells; `temperature_c` stays the headline.
- `allow_charge`, `allow_discharge`: booleans, "the BMS currently permits charging (discharging)".
- `protections`: array of names from a fixed 17-name vocabulary (`cell_overvoltage` ... `other`), in vocabulary order, no duplicates.
- `charge_voltage_limit_v`, `charge_current_limit_a`, `discharge_current_limit_a`: source-reported limits.

## Wire rules

- **Alignment.** `cell_balancing` has the same length as `cell_voltages_v` or is absent, never padded or truncated. Draft-07 cannot express equal length across two arrays, so the rule lives in the property descriptions and here. A consumer that sees a mismatch ignores `cell_balancing`.
- **Invalid reads.** A bad slot is `null`, never `0`, and the array keeps its length. No answer from the BMS means nothing is published, not an array of `null`s.
- **Replacement.** A retained array is replaced wholesale at its new length when the cell count changes, with the scalars republished in the same cycle. A cell count that is unknown means the arrays are absent, never `[]`. A property that disappears is cleared with an empty retained payload (ADR 0008).
- **Derived scalars.** `cell_voltage_max_v`/`min_v`/`delta_v` are computed over the non-null cells and omitted with fewer than 2. A driver that derives them publishes all three or none, in the same cycle as `cell_voltages_v`; a source that reports only some (Venus) publishes what it has. This is the one exception to "publish the source's own value, not a computed one": they are a pure function of the published array.
- **`protections`.** `[]` means the source reports protections and none is active; absent means it does not report them. This is the only array where `[]` is valid. JBD protection bits 0-12 map to the first 13 names in order. Venus `/Alarms/*` map by name and only alarm level `2` is active; warnings (`1`) are ignored. A set bit or alarm with no name in the vocabulary publishes `other`, and the driver logs the raw value.
- **`allow_*`.** Published as the source reports them: JBD from the FET byte (bit0 = charge, bit1 = discharge), Venus from `Io/AllowToCharge` and `Io/AllowToDischarge`. A source with only one publishes only that one. Venus reads an unknown state as `0`, so a `false` from Venus cannot be told from "BMS not reporting"; this is a known ambiguity, not detected.
- **Cell extremes without cells.** `cell_voltage_max_v`/`min_v`/`delta_v` may be published without `cell_voltages_v` when the source reports them but no per-cell array (a Lynx Smart BMS through Venus). Only a driver that derives them from the array omits them when the array is absent.
- **`cell_balancing` false vs unknown.** A clear balance bit from a source that reports one is `false`; `null` is a cell whose state is unknown (for example an unreadable cell). Venus reads a BMS that does not report balance state as `0` on `Balances/Cell<N>`, so Venus publishes `false` there: the same unknown-reads-as-false ambiguity as `allow_*`, accepted and not detected.
- **Invalid-read encodings (implementation choices).** `driver-wattcycle` treats a cell reading of `0` or `0xFFFF` mV and a probe word of `0` or `0xFFFF` as unreadable (`null`); no real fault was captured, so this is an assumption. A `0x04` payload whose length differs from the `0x03` `cell_count`, or a count above 32 cells (16 probes), publishes no cell data rather than a padded or truncated array.
- **Cell count never shrinks on Venus.** Venus does not announce that a path is gone, so `driver-venus` keeps old cell slots until it restarts after a BMS reconfigures to fewer cells.
- **Limits.** Source-reported only. A driver never computes a limit, and `driver-wattcycle` never publishes them.
- **One instance, at most one BMS.** Cell data describes exactly the one BMS behind an instance; it is never concatenated or merged across packs. Several BMS units are several instances. A Venus service that already combines packs publishes whatever cell paths it exposes, or none. A consumer never borrows cells from a sibling instance of the same bank when the Primary battery instance has none (selection, not fusion, ADR 0019). There is no aggregate flag.

## Why

- **The cells belong to the battery.** A BMS is the hardware, not this project's entity name (ADR 0008 names entities for what the hardware _is_). A pack's cells, probes, balancing and protections are facts about the same bank whose state of charge `battery` already reports, from the same Driver and the same connection. A separate `bms` entity would need a second instance id, a join back to its `battery`, and a second Driver for no reason a consumer can use.
- **Optional keeps every source valid.** A shunt has no cells, a BMS-less bank has no probes. Consumers already tolerate absent optional properties, so nothing existing changes.
- **Arrays follow ADR 0009.** A per-cell reading is one atomic snapshot of the pack; one retained array per property means a consumer never reads half of an update. `null` for a bad slot follows the same reasoning as a missing yield day: a gap and a zero are different things.

## Limits this accepts

- **No per-instance staleness signal.** Both Drivers set a retained LWT on `renewvan/<driver>/health`, one per Driver, which covers only process or connection death. A BMS that goes silent while its Driver is alive leaves the last values retained and health `online` (`driver-wattcycle` catches `BleError` and retries without publishing). That is true of every existing `battery` property; a per-instance staleness signal would touch every property and both Drivers and is its own effort (not decided here).
- **JBD bit layouts are unverified on hardware.** The DH04 capture (`research/dh04-capture`) read the balance words (`0x0000`), the protection word (`0x0000`) and the FET byte (`0x03`) only at rest. The protection bit order above and the FET bit meanings come from the JBD specification and must be confirmed against a real balancing event or a safely forced protection during implementation.
- **Venus cell paths are config-dependent.** Every per-cell, per-probe and balancing path is a `dbus-serialbattery` extension whose layout depends on its configuration (`research/venus-cell-paths`); Victron documents only the limits and `Io/AllowTo*`. This van's GX has no `dbus-serialbattery` service, so the Venus mapping is written for other users' vans and tested from the research, not on this hardware.

## Rejected alternatives

- **A separate `bms` entity (and Driver).** Rejected for the reasons above; it was the prior plan.
- **Always publish a fixed-length cell array.** Rejected: cell count varies (4 for 12 V, 8 for 24 V) and padding would invent cells.
- **Drivers computing charge and discharge limits.** Rejected: the limits are the BMS's own numbers or nothing.
- **Fusing a shunt instance and a BMS instance, or merging packs.** Rejected by ADR 0008 and ADR 0019.

## Why this is an ADR

Hard to reverse: these are new retained topics and schema properties that two Drivers, the dashboard and the logger will rely on, and a 17-name vocabulary that is a public contract. Surprising without context: a reader will wonder why a BMS's data is not its own entity when `CONTEXT.md` once said it would be, and why derived scalars are allowed against "publish the source's own value". The result of a real trade-off: a separate entity is a cleaner fit for the word "BMS" but costs a join and a Driver; folding it into `battery` keeps one instance per bank source.

## See also

- `docs/adr/0008-node-naming-and-entity-neutrality.md`: entity neutrality; one source per id; a shunt and a BMS are two views.
- `docs/adr/0009-solar-yield-history-as-one-retained-array.md`: one retained array, `null` for a gap.
- `docs/adr/0019-entity-instances-name-their-driver-and-shared-primary-battery.md`: `driver`, the Primary battery, selection not fusion.
- `schema/battery.schema.json`, `schema/demo/battery.wattcycle.demo.json`: the contract and the demo scenario.
- `CONTEXT.md`: **Battery**.
