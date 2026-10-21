---
status: accepted
---

# Rename `Node` to `Device`, and `Host` to `Board`

The project's word for "anything that publishes one specific device or feed onto the renewvan bus" changes from **Node** to **Device**, and the word for the physical hardware it runs on changes from **Host** to **Board**. Every artifact derived from the old words changes with them, as a clean cutover with no alias or shim.

| Was                                                                | Now                                                                    |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Repo / image / compose service `node-<source>`                     | `driver-<source>` (`driver-tank`, `driver-venus`, `driver-demo`, ...)  |
| `node-esp32-relay` (a Node whose hardware is a relay board)        | `driver-esp32-relay`                                                   |
| Python package `node_<source>`, `node_info.py`                     | `driver_<source>`, `device_info.py`                                    |
| `NODE_VERSION` build arg / env var                                 | `DEVICE_VERSION`                                                       |
| `schema/node-manifest.schema.json`, `schema/node-info.schema.json` | `schema/device-manifest.schema.json`, `schema/device-info.schema.json` |
| JSON key `node` (manifest, info, fleet entry), fleet key `nodes[]` | `device`, `devices[]`                                                  |
| JSON key `host_serial`                                             | `board_serial`                                                         |
| Topics `renewvan/<node>/info`, `renewvan/<node>/health`            | `renewvan/<device>/info`, `renewvan/<device>/health`                   |
| INI section `[node]` in `config.ini`                               | `[device]`                                                             |
| Key slug rule `(?!node-)`                                          | `(?!driver-)`                                                          |
| Glossary: Node, Node manifest, Node info, Host                     | Device, Device manifest, Device info, Board                            |

Unchanged on purpose: entity names and topics (`renewvan/<entity>/<id>/<property>`), the `demo` namespace, the "device model" (the schema set as a whole), container names, and every use of "node" that is not this project's word: Node.js, `node_modules`, the sysfs backlight node, graph nodes in the dashboard's power-flow diagram (`NodeId`, `flow.nodes`), `React.ReactNode`, and ESPHome's own wording when quoted.

## Why

A third party sells a product line called NODE (marked with the registered-trademark symbol) that attaches to a central unit, which is the same hub-plus-ESP32 shape as this project. I could not verify that any registration exists. "Node" is also an ordinary networking word, so any such mark is likely narrow, and lowercase descriptive use in documentation is low risk. The risk rises sharply when "node" is part of a repo, image, package, domain or hardware name, which is exactly how this project used it (`renewvan/node-*`). The project is pre-v0.1 with no external adopters, so renaming now costs a scripted replace; after hardware ships or others adopt the topics it costs far more. This is general information, not legal advice, and the register checks (DPMAregister, EUIPO eSearch plus, WIPO, TMview) are still to be run by the maintainer.

- **`device` is the common word for the thing a user sees.** Home Assistant, Zigbee2MQTT, Tasmota, Victron Venus OS and RV-C all say device. It is short in topics.
- **`board` is neutral and exact for the hardware.** It replaces `Host`, which also named the Pi that runs the Docker stack and the "Pi-host plugins", so one word did two jobs.
- **Repos keep the `driver-` product prefix.** The project's earlier artifact word (`driver-*`, ADR 0002) is restored for repos, images, packages, services and MQTT client ids, so every artifact name is a single uniform `driver-<source>`, including the relay (`driver-esp32-relay`, ADR 0012's board-named exception is unchanged apart from the prefix). The concept and the wire contract use Device (a Device is published by a `driver-<source>` artifact), so no wire field or schema carries `driver`.
- **Avoided:** `module` (the third party's word for its own units), `satellite` (a voice device in Home Assistant), and `brain` (the third party's name for its central unit).

## Rules this sets

- A Device's key is its repo name without the `driver-` prefix (`driver-venus` is device `venus`; `driver-esp32-relay` is device `esp32-relay`). It is the topic segment, and the key slug rule rejects a `driver-` prefix and the reserved key `demo`.
- Never write the registered-trademark symbol after "node" or "device".
- The role-prefix rule of `CONTEXT.md`'s **Naming convention** is unchanged except for the prefix word.
- The retained topics of the old keys do not move: `renewvan/<key>/info` and `renewvan/<key>/health` keep the same `<key>`, only the payload field `node` becomes `device`. A broker that still holds an old retained `info` carrying `node` fails the new schema, so clear retained `renewvan/+/info` once at cutover.

## Supersedes (naming only)

ADRs 0002, 0008, 0011 and 0012 are historical records and are left as written, so they still say Node, Host and `node-*`. Read them with this table. Their decisions stand: source naming (0008), the manifest and retained info topic (0011), and the relay-board exception (0012). This ADR changes only the words.

## Why this is an ADR

Hard to reverse: repo, image, package and topic-payload names are public contracts that other repos and deployments pin by name. Surprising without context: the accepted ADRs still say Node while the code says Device. The result of a real trade-off: keeping `node` was free today and risked a trademark dispute later, at the cost of a coordinated rename across fourteen repos now.

## Cutover and what is not done here

- No compatibility shim: no external adopters existed (same reasoning as ADRs 0002 and 0008).
- This change renames local directories, code, schema and docs. Renaming the GitHub repositories (`gh repo rename`) and publishing `ghcr.io/renewvan/driver-*` images is a separate step; until it is done the `image:` tags in `docker-compose.yml` name images that do not exist yet, so a deploy fails at pull. Pushes to old remotes keep working through GitHub's redirect only after the repo is renamed.
- Every running van must be redeployed with all repos together, because the payload field names changed on the wire.

## See also

- `docs/adr/0002-driver-to-node-rename.md`, `docs/adr/0008-node-naming-and-entity-neutrality.md`, `docs/adr/0011-node-manifest-and-retained-info-topic.md`, `docs/adr/0012-board-named-relay-node.md` — the decisions whose wording this changes.
- `CONTEXT.md` — `Device`, `Board`, `Device manifest`, `Device info`, `Naming convention`.
