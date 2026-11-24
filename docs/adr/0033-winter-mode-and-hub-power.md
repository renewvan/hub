---
status: accepted
---

# Winter mode is a clean hub shutdown that the relay board finishes, and Hub power is reserved

A van parked for the winter powers the hub (Pi and router) down through one relay-board channel, and a phone powers it back on over Bluetooth (**Hub wake**, [ADR 0034](0034-hub-wake-and-direct-control-belong-to-the-mobile-app.md)). Until now nothing powered the hub down cleanly: the only way was switching that relay off, a hard power cut that risks the Pi's disk. And every client offered a wake whenever it lost the broker, which also happens with no signal or a router fault.

So **Winter mode** becomes an explicit state the user enters, and that channel becomes **Hub power**:

- **Hub power is chosen per board at flash time** (`hub_power` in `secrets/<board>.yaml` of `driver-esp32`), never in a firmware variant (two boards of one variant would both claim it), and announced in the board's **Driver info**. At most one per van; two claims are a setup error, and clients then offer no Winter mode.
- **Start winter mode** is the unretained `renewvan/hub/winter-mode/set` `true`, sent by any client after a confirm (never in Demo mode). It is refused while the heater is not `off`, and the confirm lists the relays that are on, offering to switch them off (unticked). The **Hub plugin** publishes the retained `renewvan/hub/winter-mode` `true`, sends the Hub power board its Winter mode command, and powers the Pi off through a polkit rule that lets only its service user call `login1` power-off.
- **The board finishes the shutdown.** On its Winter mode command it stores a Winter mode flag in flash and cuts Hub power after a fixed firmware delay (about 90 s), whatever the Pi has done by then. It cannot be cancelled once sent.
- **Hub power is the one exception to `ALWAYS_OFF`.** At boot it is on unless the board's Winter mode flag is set, so a board update or brownout never cuts a running hub, and Winter mode survives one. Hub wake clears the flag. The board refuses a plain MQTT `set false` on Hub power: its only way off is Winter mode.
- **Clients remember Winter mode.** Each stores the retained flag while it is connected and keeps it while the hub is gone; the mobile app offers Hub wake prominently only while it remembers Winter mode and the hub is unreachable. The memory clears only when the client reaches the bus again and the hub reports `false` (the Hub plugin publishes `false` when it boots).
- **Clients show Hub power reserved**: a tile with a Hub power badge and a Start winter mode button, no on/off switch, not grouped or renamed, last in the order (one rule in `@renewvan/core/relays`).

## Considered options

- **Offer wake whenever the hub is unreachable** (the first dashboard version). Rejected: unreachable is not asleep, so it prompted on every signal or router fault.
- **A Winter mode flag only on the phone that started it.** Rejected: a Winter mode started from the kiosk or another phone would never be known. The retained flag reaches every connected client; a phone that missed it keeps a quiet Hub wake entry in its Settings.
- **A Pi GPIO line telling the board the Pi has halted.** Rejected: new wiring for every install, when a fixed delay on the board does the same with no hardware.
- **Hub power in hub config.** Rejected: a second source of truth for what the board already knows.
- **`ALWAYS_ON` for Hub power at boot.** Rejected: a board brownout in Winter mode would wake the hub and drain the battery.
