---
status: accepted
---

# The heater is writable, with command-safety rules in the Driver

The **Heater** entity is a fuel-fired space heater that reports its own phase and faults and accepts a small set of unretained commands. The contract is `schema/heater.schema.json`; this ADR records the safety rules behind it. It is modelled on ADR 0013 (the writable relay), which it extends: a heater is a writable load that can start a flame.

**The broker accepts anonymous clients, so anyone who can reach port 1883 or the 9001 WebSocket listener can start or stop the heater.** `docker/mosquitto/config/mosquitto.conf` sets `allow_anonymous true`. This ADR does not fix it; broker authentication and ACLs are a separate, hub-wide effort. Until then the broker must only be reachable from the van's own network. This is why `commands_enabled` defaults to `false`.

## The contract

- **Topics.** `renewvan/heater/<id>/<field>`, retained for fields. The Driver key is `heater`; the manifest declares `publishes: [heater]`.
- **Unknown is absent.** A value the heater cannot supply is an empty retained payload (ADR 0008 "no value"), never `null`, never `0`.
- **Commands** (unretained, strict JSON, documented in the schema description, no separate schema files, per ADR 0013): `set` (`true`/`false`, never a toggle), `mode/set`, `setpoint_c/set` (1-30), `power_level/set` (0-9), `ventilating/set` (`true`/`false`), `clear_error/set` (`true` only).
- **Per phase.** `set` true starts only from `off` with `error_code` 0, using the settings the heater reported. `ventilating/set` true starts fan-only (using the read `power_level`) only from `off`. Settings writes are refused during `shutting_down` and while ventilating. `stop` is always accepted.

## Command-safety rules

- **Staleness.** After `stale_after_s` (5 s) without telemetry the Driver rejects every command and clears the live fields, so no consumer shows a stale phase. A consumer also treats the heater as unknown whenever the owning Driver's health is not `online` (ADR 0011).
- **No rejection topic.** A rejected command is logged at WARNING. A command is confirmed only when the retained field changes; a consumer shows "pending" and times out honestly.
- **`commands_enabled`.** Published as a retained boolean and `false` by default. Installing the Driver never gives the LAN a way to start a heater.
- **The Driver never acts by itself.** It never stops, starts or re-commands the heater on its own. After a reconnect it resyncs from telemetry and does not re-send anything. If the Pi dies the heater keeps its own cycle.
- **Retries.** `stop` is retried until it takes. Every other command is sent once, with one retry after three polls; there is no blind double-send.
- **Hardware safety stays in hardware.** Thermal cutoffs and a manual off are in the heater and never in software.

## Phase mapping (Low-Medium confidence)

`0.x` is `off`; `1.x` and `2.1`-`2.3` are `starting`; `2.4` is `warming_up`; `3.0` and `3.35` are `running`; `3.4` and `4.x` are `shutting_down`; any other code is `off`, with the raw `status_code` still published. `ventilating` is `true` only for `3.35`. **Verified on real hardware: only `0.1` and `3.35`.**

## Unverified on an AIR 2D

Every item below is a research default, not a fact, and is to be revisited after a hardware pass:

- the `0x0D` unblock and the init sequence;
- live settings changes (`mode`, `setpoint_c`, `power_level`) and the `setpoint_c` bounds;
- the `clear_error` frame;
- every status code except `0.1` and `3.35`, and so the phase mapping above.

## Rejected alternatives

- **A toggle command.** Rejected, as for the relay: not idempotent.
- **The Driver re-asserting a desired state after a restart.** Rejected: a stale "on" would light a flame the user no longer wants.
- **A software thermal cutoff or watchdog stop.** Rejected: the heater's own protection is the authority, and a Driver that stops it on a stale link would act on a guess.
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
