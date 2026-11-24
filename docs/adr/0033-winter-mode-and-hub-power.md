---
status: accepted
---

# Winter mode is a clean hub shutdown that the relay board finishes, and Hub power is reserved

A van parked for the winter powers the hub (Pi and router) down through one relay-board channel, and a phone powers it back on over Bluetooth (**Hub wake**, [ADR 0034](0034-hub-wake-and-direct-control-belong-to-the-mobile-app.md)). Until now nothing powered the hub down cleanly: the only way was switching that relay off, a hard power cut that risks the Pi's disk. And every client offered a wake whenever it lost the broker, which also happens with no signal or a router fault.

So **Winter mode** becomes an explicit state the user enters, and that channel becomes **Hub power**:

- **Hub power is chosen per board at flash time** (`hub_power` in `secrets/<board>.yaml` of `driver-esp32`), never in a firmware variant (two boards of one variant would both claim it), and announced in the board's **Driver info**. At most one per van; two claims are a setup error, and clients then offer no Winter mode.
- **Start winter mode** is the unretained `renewvan/hub/winter-mode/set` `true`, sent by any client after a confirm (never in Demo mode). It is refused while the heater is not `off`, and the confirm lists the relays that are on, offering to switch them off (unticked). The **Hub plugin** sends the Hub power board its Winter mode command and waits for the board's confirmation; only then does it publish the retained `renewvan/hub/winter-mode` `true` and power the Pi off, through a polkit rule that lets only its service user call `login1` power-off. With no confirmation in time it aborts: `false`, no power-off (see "The board confirms" below).
- **The board finishes the shutdown.** On its Winter mode command it stores a Winter mode flag in flash and cuts Hub power after a fixed firmware delay (about 90 s), whatever the Pi has done by then. It cannot be cancelled once sent.
- **Hub power is the one exception to `ALWAYS_OFF`.** At boot it is on unless the board's Winter mode flag is set, so a board update or brownout never cuts a running hub, and Winter mode survives one. Hub wake clears the flag. The board refuses a plain MQTT `set false` on Hub power: its only way off is Winter mode.
- **Clients remember Winter mode.** Each stores the retained flag while it is connected and keeps it while the hub is gone; the mobile app offers Hub wake prominently only while it remembers Winter mode and the hub is unreachable. The memory clears only when the client reaches the bus again and the hub reports `false` (the Hub plugin publishes `false` when it boots).
- **Clients show Hub power reserved**: a tile with a Hub power badge and a Start winter mode button, no on/off switch, not grouped or renamed, last in the order (one rule in `@renewvan/core/relays`).

## The board confirms before the Pi halts

Added with the wire contract (hub#213). A lost command (a WiFi blip, the board offline) must not end with a halted Pi on a Hub power relay that is still on: Hub wake would only say `already-on`, nothing could restart the Pi short of cutting its power by hand, and every client would remember a Winter mode that never happened. So the board confirms:

| Topic                               | Retained | Payload                                    | Written by              |
| ----------------------------------- | -------- | ------------------------------------------ | ----------------------- |
| `renewvan/hub/winter-mode`          | yes      | boolean (`hub-winter-mode.schema.json`)    | Hub plugin (`hub`)      |
| `renewvan/hub/winter-mode/set`      | no       | `true`                                     | any client (`command`)  |
| `renewvan/<driver>/winter-mode/set` | no       | `true`                                     | Hub plugin (`hub`) only |
| `renewvan/<driver>/winter-mode`     | yes      | boolean (`driver-winter-mode.schema.json`) | the Hub power board     |

The board stores its flag, starts its delay and publishes `true`; the plugin waits for it (about 10 s), then publishes `renewvan/hub/winter-mode` `true` and powers off. Without it the plugin republishes `false` and stays up, and the client's confirm says the board did not answer. The board publishes `false` on a boot without the flag and after Hub wake. Driver info carries `hub_power` (the relay id).

Only the `hub` account may write a board's `winter-mode/set`, never `command`: a client could otherwise skip the heater gate and the clean shutdown. The ACL lines land in `bin/broker-clients.conf` with the plugin (hub#214); a board already writes its own `renewvan/esp32-<name>/#`.

## Considered options

- **Offer wake whenever the hub is unreachable** (the first dashboard version). Rejected: unreachable is not asleep, so it prompted on every signal or router fault.
- **A Winter mode flag only on the phone that started it.** Rejected: a Winter mode started from the kiosk or another phone would never be known. The retained flag reaches every connected client; a phone that missed it keeps a quiet Hub wake entry in its Settings.
- **A Pi GPIO line telling the board the Pi has halted.** Rejected: new wiring for every install, when a fixed delay on the board does the same with no hardware.
- **Hub power in hub config.** Rejected: a second source of truth for what the board already knows.
- **`ALWAYS_ON` for Hub power at boot.** Rejected: a board brownout in Winter mode would wake the hub and drain the battery.
