---
status: proposed
---

# The heater Driver feeds a room temperature to the heater as its panel sensor (`0x11`)

_(When accepted, supersedes one rejected alternative of [ADR 0021](0021-writable-heater-and-its-command-safety-rules.md), "A Driver-side `0x11` temperature feed". Every other ADR 0021 rule stands.)_

ADR 0021 was written while the hub mostly watched the heater: the panel regulated it, and the Driver read it and sent the odd command. The project now controls the heater from the Pi, with the panel unplugged and kept only as a backup (it cannot share the heater's UART with the adapter). Without the panel, the heater has no panel sensor, so its temperature modes regulate on the heater's own sensor at the heater (mode 1) or on an external probe wired to it (mode 3). The van already has a better room sensor, a DS18B20 on the Pi (`renewvan/temperature/<id>/`), and the heater has a mode made for a reading sent to it: mode 2 regulates on the temperature the panel sends in `0x11` (one signed byte, °C; the Comfort Control sends it every 2-3 s, and the heater echoes it).

A thermostat in a hub plugin that only uses the normal commands, as ADR 0021 suggested, can only nudge the setpoint or start and stop the heater around a target. That is cruder than letting the heater regulate on the right reading, and starting and stopping a diesel heater often wears it.

## Decision

- **A new mode, `room_temperature` (wire code 2).** `mode/set "room_temperature"` makes the heater regulate on a room sensor the Driver feeds it. The Heater schema's `mode` enum gains `room_temperature`. The Driver publishes the value it sends as `room_temperature_c`, retained, and empty when it sends nothing.
- **The source is configured, not discovered.** `config.ini` names one temperature instance (`room_sensor = <temperature id>`). There is no default and no automatic choice. Without it, `room_temperature` is refused like any unknown mode.
- **The Driver sends `0x11` only in `room_temperature`, and only with a valid reading.** A reading is valid when the source's `status` is `ok`, `temperature_c` is between -30 and 50 °C, and the Driver received it live in the last 15 s (three of driver-temperature's 3 s updates). A retained value replayed when the Driver subscribes does not count, because driver-temperature leaves the last `temperature_c` on the broker when the probe fails. The value is rounded to whole degrees. It is sent once per poll, between the status and settings reads, as the panel does.
- **Starting needs a valid reading.** `set true` and `mode/set "room_temperature"` are refused (WARNING) without one.
- **A reading that goes bad stops the feed, not the heater.** The Driver stops sending `0x11`, clears `room_temperature_c` and logs one ERROR. It still never stops or re-commands the heater by itself (ADR 0021). What the heater then does is its own behaviour, and that is the gate for accepting this ADR (Blocked on).
- **Sending a reading is not a command.** `0x11` changes no setting. It is not gated by `commands_enabled` and is not retried; a missed one is replaced by the next poll's. Switching to `room_temperature` is a normal `mode/set`, gated as usual.

## Blocked on (a human-present bench check)

- With the heater in mode 2 and running, sending fixed values (`0x11` = 10 °C, then 28 °C, setpoint 20 °C): does the heater raise and then lower its power, or pause, the way it does on its own sensor?
- **When `0x11` stops while running in mode 2, what does the heater do, and after how long?** It might fall back to its own sensor, hold the last value, stop with an error, or keep going. This ADR is accepted only if that behaviour is safe without the Driver acting. If the heater keeps heating on the last value indefinitely, this ADR is rejected or reworked.
- A mode 2 start with no `0x11` sent yet: refused, an error, or a start on the heater's own sensor?

## Rejected alternatives

- **A hub-plugin thermostat using the normal commands** (the ADR 0021 suggestion). Rejected: setpoint nudging or start/stop cycling around a target is cruder than the heater's own regulation, and frequent starts wear a diesel heater. It stays the fallback if the bench check fails.
- **Sending the target temperature when there is no reading.** One open-source ESP32 bridge does this. Rejected: the heater would believe the room is at the setpoint and regulate on nothing.
- **Holding the last good reading while the sensor is out.** Rejected: a stale "room is cold" keeps the heater burning.
- **A DIY or genuine external probe (mode 3).** Not rejected, but not needed for this: it is hardware, and it does not move with the Pi's sensors. It remains available.
- **Picking the room sensor automatically.** Rejected: a probe in the fridge or the engine bay would be just as "available".

## Why this is an ADR

Hard to reverse: `room_temperature`, `room_temperature_c` and the `room_sensor` key become part of the Heater contract, and the Driver sends a frame continuously where it used to send only on a command. Surprising without context: a reader will wonder why the Driver may now send something on its own every second. It is a reading, not a command, and the heater's behaviour without it was checked first. The result of a real trade-off: it reverses an ADR 0021 rejection because the project moved from watching the heater to controlling it.

## See also

- `docs/adr/0021-writable-heater-and-its-command-safety-rules.md`: the safety rules this keeps.
- `docs/adr/0030-heater-timers.md`: the other "controlling" step.
- `schema/temperature.schema.json`: `status` and the retained-last-value behaviour.
- driver-autoterm `docs/bench-pu27-settings.md`: the PU-27's own "By T panel" is the same mode 2, with its built-in sensor.
