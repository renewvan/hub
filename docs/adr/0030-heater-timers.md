---
status: proposed
---

# Heater timers: the run time is the heater's own setting, scheduled starts belong to the Hub plugin

_(Builds on [ADR 0021](0021-writable-heater-and-its-command-safety-rules.md); every rule there still holds, in particular "the Driver never acts by itself".)_

The dashboard's heater page already has the Autoterm-style timer: **Off in** stops the heater after a time, **On in** starts it after a time (presets 30/60/90 min). It works only on the local dev heater, because the hub and the Driver have no timer field. When the Pi controls the heater, the panel is unplugged (it cannot share the heater's UART with the adapter), so the panel's own timers (the PU-27's three weekday timers, the Comfort Control's three) are not there either.

The two timers are different problems. Stopping after a time is something the heater can do itself. Starting at a time is not: the heater has no clock and no scheduled start, so something on the Pi has to send the start.

## Decision

- **Off after N minutes is the heater's run time.** Bytes 0-1 of the `0x02` settings hold the run time the panels set (PU-27 _Laufzeit_, Comfort Control _work time_, 30 min to unlimited). The heater enforces it, so the stop happens even with the dashboard closed. The Heater entity gains `run_time_min` (retained, read back from the heater like the other settings) and `run_time_min/set` (integer minutes, or `0` for unlimited), written like `setpoint_c`: read `0x02` fresh, change only bytes 0-1, confirm on the next read. If the bytes count down while running, the Driver also publishes `run_time_left_min`. The encoding is not settled (see Blocked on), so none of this ships before the bench check.
- **Scheduled starts are a Hub plugin setting.** The timers are shared state that every client must agree on, like the primary battery (ADR 0019) and the relay groups (ADR 0028), so the Hub plugin owns them: a retained JSON list on `renewvan/hub/heater-timers`, changed through `renewvan/hub/heater-timers/set`, persisted on the Pi. A timer names its heater id, a kind (`once`, at a time; `weekly`, on days of the week at a time), its run time and whether it is enabled. **On in N min** on the dashboard is a `once` timer at now + N, computed by the plugin when it receives it.
- **A timer fires the normal commands, nothing else.** At the time, the plugin publishes `run_time_min/set` and then `set` `true` on the heater's topics, as the dashboard would. The Driver applies every ADR 0021 gate unchanged: it starts only from `off` with `error_code` 0, with commands enabled and fresh telemetry, and otherwise logs a WARNING and does nothing. The plugin never enables commands, never retries, and never stops the heater itself (the run time does that). It records the outcome in the timer's entry (`last_fired`, and `started` true/false from whether `phase` left `off` within a minute) so the dashboard can say "the 07:00 timer did not start the heater".
- **Every timed start has a finite run time.** A timer start is unattended, so the plugin refuses a timer with an unlimited run time (`0`). The default is 60 min, the maximum 12 h, the panels' finite range.
- **No clock, no timer.** The Pi has no real-time clock, so after an offline boot its time is wrong until NTP or the GPS sets it. The plugin fires a timer only while the system clock is synchronized. A timer more than 2 min late (Pi off, clock not yet synced) is skipped and recorded as missed, never fired late.
- **The broker account.** Today only the `command` account writes `set` topics (ADR 0025). The `hub` account gains write on exactly `renewvan/heater/+/set` and `renewvan/heater/+/run_time_min/set`, nothing else under `renewvan/heater/`.

## Open questions

- **After a Pi or plugin restart.** The PU-27 keeps its timer settings across a power loss but turns the timer off ("Timeraktivierung wird zurückgesetzt"). The proposal keeps `weekly` timers enabled (a van whose Pi rebooted overnight should still warm up in the morning) and drops a `once` timer whose time passed while the plugin was down. To decide before acceptance.
- **The panel's own timers.** When the panel is plugged in as the backup, its timers are separate and invisible to the hub. Nothing reconciles the two; the dashboard should say so.

## Blocked on

- The run-time encoding: one 16-bit minute count (`00 78` = 120 min, `FF FF` = unlimited) or something else, whether `FF` in a write means "keep" as the Comfort Control uses it, and whether the bytes count down. The AIR 2D here reads `01 00`, which as a minute count is 256 min, so a long run may already stop by itself. Bench checklist: driver-autoterm `docs/bench-pu27-settings.md`, Part C.

## Rejected alternatives

- **Timers in the Driver.** Rejected: a Driver that starts the heater because a time came breaks "the Driver never acts by itself" (ADR 0021), and it would have to keep schedule state across its own restarts, which ADR 0021 forbids for commands.
- **Timers in the browser or kiosk.** Rejected: a closed tab, a sleeping phone or a kiosk reload loses the timer.
- **A general scheduler service.** Rejected for now: the heater is the only consumer. If relays get schedules, this setting can move into one, with the same firing rule.
- **Software stop for Off in.** Rejected: the heater's own run time does it without a Pi in the loop.
- **Unlimited timer starts.** Rejected: an unattended start must end by itself.

## Why this is an ADR

Hard to reverse: `run_time_min`, `renewvan/hub/heater-timers` and the extra `hub` ACL lines become a wire contract the Driver, the plugin and the dashboard depend on. Surprising without context: a reader will wonder why one timer lives in the heater and the other in a plugin, and why the plugin, and not the Driver, starts a flame on a schedule. The result of a real trade-off: a timer in the Driver is simpler, but it is the first thing ADR 0021 rules out.

## See also

- `docs/adr/0019-entity-instances-name-their-driver-and-shared-primary-battery.md`, `docs/adr/0028-relay-groups-are-a-shared-hub-setting.md`: shared settings owned by the Hub plugin.
- `docs/adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md`: the accounts and ACLs.
- `schema/heater.schema.json`: the Heater contract this extends.
