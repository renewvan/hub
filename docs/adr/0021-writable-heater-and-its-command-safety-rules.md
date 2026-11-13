---
status: accepted
---

# The heater is writable, with command-safety rules in the Driver

The **Heater** entity is a fuel-fired space heater that reports its own phase and faults and accepts a small set of unretained commands. The contract is `schema/heater.schema.json`; this ADR records the safety rules behind it. It is modelled on ADR 0013 (the writable relay), which it extends: a heater is a writable load that can start a flame.

Who may publish a command is decided by the broker's per-client accounts and ACLs ([ADR 0025](0025-broker-has-per-client-accounts-and-anonymous-read-only.md), [ADR 0026](0026-anonymous-clients-are-refused-after-the-cutover.md)). `commands_enabled` still defaults to `false`, so a heater never accepts commands until someone turns that on.

## The contract

- **Topics.** `renewvan/heater/<id>/<field>`, retained for fields. The Driver key is `heater`; the manifest declares `publishes: [heater]`.
- **Unknown is absent.** A value the heater cannot supply is an empty retained payload (ADR 0008 "no value"), never `null`, never `0`.
- **Commands** (unretained, strict JSON, documented in the schema description, no separate schema files, per ADR 0013): `set` (`true`/`false`, never a toggle), `mode/set`, `setpoint_c/set` (1-30), `power_level/set` (0-9), `thermostat/set` (`true`/`false`), `ventilating/set` (`true`/`false`), `clear_error/set` (`true` only).
- **Per phase.** `set` true starts only from `off` with `error_code` 0, using the settings the heater reported. `ventilating/set` true starts fan-only (using the read `power_level`) only from `off`; it is confirmed only by status `3.35`. Settings writes are refused during `shutting_down` and while ventilating, and accepted in `standby`. `stop` is always accepted, whatever the phase or run time. `clear_error/set` is refused by the Driver (WARNING) until verified on hardware, `ventilating/set` is accepted from driver v0.4.0 but its frame is not yet bench-verified, and the `0x0D` unblock is never exposed.

## Command-safety rules

- **Staleness.** After `stale_after_s` (5 s) without telemetry the Driver rejects every command and clears the live fields, so no consumer shows a stale phase. A consumer also treats the heater as unknown whenever the owning Driver's health is not `online` (ADR 0011).
- **No rejection topic.** A rejected command is logged at WARNING. A command is confirmed only when the retained field changes; a consumer shows "pending" and times out honestly.
- **`commands_enabled`.** Published as a retained boolean and `false` by default. Installing the Driver never gives the LAN a way to start a heater. Its config-file value is only the starting point: `renewvan/heater/<id>/commands_enabled/set` (strict JSON `true` or `false`) flips it for the running Driver, and is the one command accepted while commands are off, because it is what turns them on. The change is not persisted, so a Driver restart returns to the config file. Turning commands off never stops a running heater. This gives anyone who can reach the anonymous broker the same reach as the start command it unlocks, so it adds no exposure beyond the one named above; the dashboard asks for a confirmation first.
- **The Driver never acts by itself.** It never stops, starts or re-commands the heater on its own. After a reconnect it resyncs from telemetry and does not re-send anything. If the Pi dies the heater keeps its own cycle.
- **Settings are read, never assumed** (driver-autoterm v0.5.0). The Driver re-reads the `0x02` settings every 5 polls, so a change made on the heater's own panel is published within seconds. Start, ventilate and every settings write read them fresh first (a failed read rejects the command) and send back the whole payload with only the commanded field changed: bytes the Driver does not understand (`use_work_time`, `work_time`, `wait_mode`) keep what was picked on the panel, so a command from the hub never undoes a panel mode such as Thermostat.
- **Retries.** `stop` is retried at most 3 attempts, spaced 3 polls apart, then one ERROR is logged and nothing more is sent until a new stop arrives. A settings write (`mode`, `setpoint_c`, `power_level`) is confirmed by the next `0x02` settings read; a mismatch is "unconfirmed": one WARNING naming the command and phase, nothing published. Every other command is sent once, with one retry after three polls and the same WARNING on failure. The exception is ventilate: if the heater answers `0x23` with any start other than `3.35`, one ERROR is logged and the frame is not sent again (the Driver still does not stop the heater). A single start or stop frame is sufficient (2026-11-02 capture); there is no blind double-send.
- **Hardware safety stays in hardware.** Thermal cutoffs and a manual off are in the heater and never in software.

## Phase mapping (Low-Medium confidence)

The Driver maps the status code through a table. The state names are ours, not the heater's.

| Status code                                     | `phase`                                            |
| ----------------------------------------------- | -------------------------------------------------- |
| `0.x` (seen: `0.1`)                             | `off`                                              |
| `1.0`, `1.1`, `2.0`, `2.1`, `2.2`, `2.6`, `2.3` | `starting`                                         |
| `2.4`                                           | `warming_up`                                       |
| `3.0`                                           | `running`                                          |
| `3.35`                                          | `running`, `ventilating` true (unverified default) |
| `3.4`, `3.5`                                    | `standby` (Thermostat pause, burner off)           |
| `4.0`                                           | `shutting_down`                                    |

`1.0` means "start accepted" (seen right after a start on 2026-11-02). There is no `fault` phase: `error_code` is independent of `phase`.

**An unknown code is not `off`.** The Driver publishes an empty retained `phase` and the raw `status_code`, logs one WARNING per new code, and accepts only `stop` until the code is mapped. The schema therefore makes `phase` optional, absent only while the code is unmapped.

**Verified on real hardware (2026-11-02, one start-run-stop cycle, error 0 throughout):** `0.1`, `1.0`, `1.1`, `2.0`, `2.1`, `2.2`, `2.6`, `2.3`, `2.4`, `3.0`, `4.0`. **Verified 2026-11-13 (Thermostat run):** `3.4` (burner off, purging) then `3.5` (waiting); raising the setpoint re-ignited the heater (`2.0`-`2.4`) with no start command. A stop goes `4.0` then `0.x`, never through `3.4`. **Still unverified:** `3.35`.

## Unverified on an AIR 2D

Verified and no longer listed here: the `0x0F` status poll, the `0x02` settings read, live `mode`/`setpoint_c`/`power_level` writes and Thermostat (`thermostat/set`, the `wait_mode` byte; driver-autoterm, 2026-11-13), and that the init sequence is not needed (`send_init` defaults off). Every item below is an unverified default, not a fact, and is to be revisited after a human-present bench check:

- the `0x0D` unblock (never exposed);
- the `setpoint_c` bounds, and stop from `standby`;
- the `clear_error` frame (refused);
- status code `3.35`, and the `0x23` ventilate frame behind `ventilating/set` (accepted from v0.4.0; the first real send needs a human-present bench check);
- stop during `1.x`/`2.x` (early stop);
- which panel mode `thermostat` (`wait_mode` 1) is. The Comfort Control manual (§3.3) says Thermostat stops combustion _and_ the fan at the target, while Heat + ventilation stops combustion and keeps ventilating until 5 °C below it. The 2026-11-13 run showed the fan at about 2400 rpm in `3.5`, which matches Heat + ventilation unless the fan byte is wrong. Until a check at the heater (in `3.5`, is air still blowing? then `wait_mode` 3), "Thermostat" in this ADR means "burner pauses at the target", not the panel's mode of that name.

## Rejected alternatives

- **A toggle command.** Rejected, as for the relay: not idempotent.
- **The Driver re-asserting a desired state after a restart.** Rejected: a stale "on" would light a flame the user no longer wants.
- **A software thermal cutoff or watchdog stop.** Rejected: the heater's own protection is the authority, and a Driver that stops it on a stale link would act on a guess.
- **A Driver-side `0x11` temperature feed.** Rejected: the Driver never sends `0x11`; a future thermostat would be a hub plugin using the normal commands.
- **A per-command rejection topic.** Rejected: the retained field changing is the single confirmation path.
- **Commands enabled by default.** Rejected: the broker is anonymous.
- **Modelling the heater as a Relay.** Rejected: a relay latches an output and knows nothing about the load; a heater has phases and faults.

## Why this is an ADR

Hard to reverse: the topics, payloads and safety rules are a public wire contract that the Driver, dashboard and logger depend on. Surprising without context: a reader will wonder why there is no rejection topic, why the Driver never stops the heater itself, and why commands are off by default. The result of a real trade-off: auto-stop and auto-restore are safer-looking but act on stale guesses.

## See also

- `docs/adr/0008-node-naming-and-entity-neutrality.md`: brand-neutral naming; the empty retained payload as "no value".
- `docs/adr/0011-node-manifest-and-retained-info-topic.md`: Driver health.
- `docs/adr/0013-writable-relay-and-its-wire-contract.md`: the writable-entity precedent.
- `schema/heater.schema.json`: the machine-checked contract.
