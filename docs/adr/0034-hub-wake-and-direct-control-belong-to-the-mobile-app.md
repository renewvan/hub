---
status: accepted
---

# Hub wake and Direct control belong to the mobile app, over a challenge-response BLE contract

**Hub wake** first shipped as a dashboard page using Web Bluetooth. Web Bluetooth does not exist in any iOS browser, and the page could only open from a still-cached PWA shell, at exactly the moment the hub that serves it is off. Nobody used it. The relay board's Bluetooth is also wanted for more than wake: switching lights when the hub is down.

So the relay board's Bluetooth is the mobile app's, and only the mobile app's:

- **The dashboard offers no Hub wake** (removed in renewvan/apps). It keeps **Start winter mode** ([ADR 0033](0033-winter-mode-and-hub-power.md)), which needs the bus, not Bluetooth.
- **Direct control**: while the **Renewvan bus** is unreachable, the mobile app switches a board's channels over Bluetooth, bypassing the hub. Whenever the bus is reachable it is the only path, so a relay never gets commands two ways and the bus stays the source of truth; the board publishes its real states once the hub is back. Each board allows only the channels listed at flash time (`ble_channels` in `secrets/<board>.yaml`, none by default); **Hub power** is never listed, and Hub wake is its one Direct control action.
- **Every BLE command is challenge-response.** The board offers a single-use nonce; the app writes `HMAC(secret, nonce | channel | on/off)`. The first wake protocol's plaintext `<secret>|on` is dropped: harmless to replay when it can only power the owner's hub, not when a command can switch a load off and on. The message format lives transport-free in `@renewvan/core` (`boardBle`); the app owns the Bluetooth stack and the HMAC primitive.
- **The secret is typed once per board** and kept in the phone's secure store, never sent over the bus (anonymous clients read it, ADR 0025). Using one secret on every board of a van is setup advice, not a rule.
- **Each board advertises its Driver key** (`esp32-<board>`) as its BLE name, so the app can show the Driver's `name` and wake only the board that announced Hub power.

## Considered options

- **Keep Web Bluetooth in the dashboard beside the mobile app.** Rejected: two BLE clients for one contract, one of them unable to run on iPhones or to open with the hub off.
- **Keep the plaintext secret.** Rejected once commands could switch loads: a sniffed command could be replayed.
- **Direct control alongside the bus while the hub is up.** Rejected: two command paths for one relay, and a state the bus no longer owns.
- **Third-party BLE buttons or apps talking to the board.** Out of scope: pairing other vendors' hardware is its own feature.
