---
status: accepted
---

# A Node whose hardware is a relay board is named by host and board

The ESP32 relay Node is renamed `node-esp32-relay` and its node key becomes `esp32-relay`, so it publishes `renewvan/esp32-relay/info` and `renewvan/esp32-relay/health`. This is a deliberate exception to the source-naming rule in ADR 0008, and it is bounded: it applies to a Node whose hardware _is_ a purchasable relay board. A future ESP32 sensing Node (a temperature probe on the same kind of board) is still named for its source (`node-temperature`-style), so the host does not leak into sensing Node names.

The key is the mechanical derivation of the repo name under the manifest schema (ADR 0011): no `node-` prefix, and the key equals the topic segment. `renewvan/node-esp32-relay` is node `esp32-relay`. The `esp32-relay` key does not collide with the older `relay` key, which the retired 8-channel Node published under; see the retired-key rule below.

## Why

- **The product is the board, not a protocol.** `node-venus` is named for the feed it remaps; there is no "relay protocol" to name a Node for. What distinguishes this Node is the hardware it is: an ESP32 driving two chained shift registers into sixteen relays. Naming it for that hardware is the honest name.
- **`relay` must stay free.** The 8-channel HW-578 module wired straight to the Pi's GPIO is a separate, later Node (`node-relay`). Both Nodes publish the `relay` entity. Keeping `node-relay` for the ESP32 would collide with it, and one name cannot belong to two Nodes with different hardware, configuration and failure modes.
- **A host-only key breaks on the second ESP32 Node.** `esp32` would name the Host (CONTEXT.md: "a node is never a host"), and a second ESP32 Node would need a different key, leaving `esp32` meaning one arbitrary Node.

## Rules this sets

- A Node whose hardware is a purchasable relay board may be named by host and board. Every other Node is named for its source (ADR 0008), including a future ESP32 sensing Node.
- A new Node's key must not equal a retired Node's key while that Node's retained topics (`renewvan/<key>/info`, `renewvan/<key>/health`) remain on the broker, or an old retained info would look like the new Node's. The retired 8-channel Node's `relay` key is therefore not reused until its retained topics are cleared; the cutover clears them (ADR 0002 and ADR 0011 are the precedent for treating Node names and retained info as public contracts).
- The repo is renamed (`gh repo rename`) before the name `node-relay` is ever reused. Renaming keeps the same repo, tags and the old URL's redirect; creating a new `node-relay` later breaks that redirect. That loss is accepted knowingly: there are no external adopters.

## Rejected alternatives

- **Keeping `node-relay`.** No rename work. Rejected: it collides with the later Pi-wired Node, which is the natural owner of that name.
- **`node-esp32`.** Names the Host, not the product, and breaks on a second ESP32 Node. Rejected.
- **Source-only naming.** Consistent with ADR 0008 with no exception. Rejected because a relay board has no source in the ADR 0008 sense: nothing is remapped and nothing is sensed, so the rule produces no name.

## Why this is an ADR

Hard to reverse: the repo name, node key and the two retained topics are public contracts that other repos and deployments pin by name. Surprising without context: a reader will see `node-esp32-relay` next to `node-venus` and `node-tank` and wonder why one Node carries its host. The result of a real trade-off: source-only naming is more uniform, at the cost of an unnameable Node and a name collision.

## See also

- `docs/adr/0008-node-naming-and-entity-neutrality.md` — the source-naming rule this is an exception to. It still governs every sensing Node.
- `docs/adr/0002-driver-to-node-rename.md` — the precedent for treating artifact names as public contracts.
- `docs/adr/0011-node-manifest-and-retained-info-topic.md` — the node-key rule and the retained `info` topic.
- `docs/adr/0013-writable-relay-and-its-wire-contract.md` — the contract this Node publishes.
- `CONTEXT.md` — `Naming convention`, `Node artifact naming`, `Relay`.
