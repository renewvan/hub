---
status: accepted
---

# Every Node describes itself with a manifest and a retained info topic

Every Node ships a small `manifest.json` at the root of its repo describing itself, and publishes it, plus its build-stamped version and its Host's serial, as one retained **Node info** message on `renewvan/<node>/info`. A consumer (today the dashboard's Settings › Nodes page and Firmware page) reads it to show which Nodes are on the van, what version each runs, what each publishes, and which Host it runs on, in the style of Venus OS's per-device "Device" page.

Before this, a Node's only presence on the bus was a bare `online`/`offline` on `renewvan/<node>/health` (and the ESP32 relay had none). Nothing reported a Node's identity, version, or what it publishes, so the dashboard could list entity _types_ but not Nodes.

The contract is two JSON Schemas in this repo, validated by `schema/validate-examples.sh`: `schema/node-manifest.schema.json` (the file in a Node repo) and `schema/node-info.schema.json` (the published payload). The authoring steps are in `docs/authoring-a-node.md`.

Concretely, this decision:

- **Manifest.** Required `node` (the topic key), `product`, `source`, `publishes` (non-empty list of entity types, a static enum); optional `manufacturer`, `homepage`. `version` and `host_serial` are forbidden in the manifest.
- **Info.** The manifest fields plus a required `version` and an optional `host_serial`. Published retained, on **every connect, immediately before `online`**, and left retained when the Node goes offline or its last will fires, so a consumer can show "offline, last seen vX".
- **Topics.** `renewvan/<node>/info` and `renewvan/<node>/health` have three segments, so neither can be mistaken for an entity topic (`renewvan/<entity>/<id>/<property>`, four segments) and the logger's four-segment entity filter ignores both.
- **Reserved key.** `demo` is rejected as a Node key by the schema. `renewvan/demo/` is the Demo namespace (ADR 0010), and `renewvan/demo/health` is not a Node's health topic.
- **Version source.** For the six Python Nodes the git **release tag** is the only version source: the release workflow passes it as a build argument that becomes an environment variable the Node reads, and a Node with none reports the literal `dev`. The `version` file and `__version__` are deleted. The ESP32 relay differs: the owner flashes a locally built binary with their own secrets, so a CI binary is not what runs on a van, and a flash script derives the version from the owner's checkout (`git describe --tags --always --dirty`). An untagged tree flashes with a hash version and the firmware also defaults to `dev`.
- **Host serial.** A support identity for display, never a join key. On a Pi it is the `Serial` line of the host's `/proc/cpuinfo`, which a default container can read, so no compose or Dockerfile change is needed; the relay publishes its MAC as `AA:BB:CC:DD:EE:FF`. It is omitted (never faked, never `unknown`) when it cannot be read, or is empty or all zeros. Every Node on one Pi publishes the same value.
- **No shared package.** Each Python Node copies one small helper (about 15 lines) that loads the manifest, adds the stamped version and the serial, and serialises it.
- **Scope.** Host-level plugins (the kiosk and Tailscale processes in `plugins/`) are not Nodes: no manifest, no info, no Device page. `node-demo` is a simulator, not a Node: no manifest or info of its own, though it publishes a simulated fleet's info and health under the Demo namespace.
- **Entity views stay under Nodes.** The existing entity settings views (tanks, temperature) keep their place in Settings and are reached from a Node's "Publishes" rows. They are not re-parented under the Node in the breadcrumb.

## Why

- **The manifest is the single source of the static fields, and the version is deliberately not in it.** Versions already existed in up to four places per repo (a `version` file, `pyproject.toml`, `__version__`, the git tag) and disagreed. Forbidding `version` in the manifest schema means a hand-edited version can never disagree with the release, and a repo's CI rejects it before a release.
- **One retained message, published before `online`.** A consumer that sees `online` is guaranteed to find info already retained, so it never shows an online Node with no identity. Republishing on every connect heals a broker that lost its retained state.
- **Info stays retained when the Node dies.** Clearing it on offline would make "offline, last seen v0.1.0" impossible, and that is the most useful thing the page can tell an owner whose Node dropped. The cost is that a Node removed for good leaves a stale retained info behind; cleaning it up is out of scope.
- **A schema in the hub, a pinned ref in each Node repo.** Every Node repo's CI fetches the manifest schema from this repo at a pinned ref and validates `manifest.json`, the same way entities already use the hub as their contract. A hub-side check fails when an entity schema exists that the static `publishes` enum does not list, so a typo cannot invent an entity type and a new entity cannot be forgotten.
- **Home Assistant is a mapping, not a vocabulary.** The fields map cleanly onto an HA device block (`product` to model, `version` to `sw_version`, `host_serial` to `serial_number`, `homepage` to `configuration_url`, `node` to identifiers), recorded in the schema descriptions only. Nothing HA-shaped goes on the wire, so a future bridge is a consumer, not a contract change.
- **Copying the helper keeps releases independent.** ADR 0001 split the Node repos so each has its own image and release, and a shared Python package would re-couple them: a fix to the helper would need a release of the package and a bump in six repos. About 15 lines duplicated is cheaper than that coupling, and divergence is caught by the manifest CI gate, not by a shared import. A later "extract a common package" should weigh this choice, not treat the duplication as an oversight.
- **The host serial's privacy cost is accepted knowingly.** The serial is published as-is on a bus that may leave the van (a remote broker, a Tailscale-reachable one). No primary source covers whether a SoC serial is sensitive in that setting, so this is a judgement, not a finding. `host_serial` is optional in the schema, so hashing it or making it opt-in later is a schema-compatible change. Demo mode never shows the real Pi's serial.
- **Host-level plugins have a different contract.** They run on the Pi but are not MQTT Nodes in the glossary's sense (`CONTEXT.md`: "plugins are not MQTT nodes"), publish single status topics, and have nothing to say about entities. Giving them a manifest would stretch **Node** to cover them.

## Rules this sets

- A Node's `node` key is a lowercase slug with no `node-` prefix (repo `renewvan/node-venus` is node `venus`) and must equal the topic segment it publishes under. `demo` is reserved; the schema, the dashboard's pattern and the demo validator must all reject it and must not drift.
- Info is published before `online` on every connect and stays retained. `health` stays a retained bare word (`online`/`offline`) with a last will.
- `version` comes from the release stamp or is `dev`. It is never read from the manifest and never hand-edited.
- `host_serial` is omitted when unavailable. Never publish a placeholder.
- A new entity schema must be added to the `publishes` enum in both `node-manifest.schema.json` and `node-info.schema.json`; `schema/validate-examples.sh` fails otherwise.
- A consumer must tolerate a Node that publishes no info (not yet migrated), and a Node with info but no health (unknown, not online and not offline).
- Two Nodes publishing the same entity type both show the same instances in a consumer that joins by `publishes`. Per-instance attribution is an accepted limit, not a bug.

## Rejected alternatives

- **A shared Python package for the helper.** One source of truth for the loader, serial parse and stamp, and no drift. Rejected for the release-coupling reason above (ADR 0001); the manifest CI gate catches divergence instead.
- **Keeping `version` in the manifest, or in the repo's `version` file.** One fewer stamping step. Rejected because it is exactly how the four version sources came to disagree; the git tag is the only thing a release is actually cut from.
- **Deriving the Node list from entity topics and the health topics alone.** No new wire contract. Rejected because neither carries a version, a product name, or what a Node publishes, and the relay has no health topic at all.
- **Clearing info when a Node goes offline.** A tidier broker. Rejected because it removes "offline, last seen vX" and leaves an offline Node with no identity.
- **Including the Home Assistant device block on the wire.** A bridge could consume it directly. Rejected because it bakes one consumer's vocabulary into a public contract other repos pin; the mapping lives in the schema descriptions instead.
- **Hashing the host serial, or publishing it only on opt-in.** Removes the privacy cost. Not done now: a hashed serial cannot be compared with the one printed on a board, and the field is optional, so either remains a schema-compatible later change.
- **Reading the relay's version from a CI-built binary.** One version path for every Node. Rejected because the owner flashes a locally built binary with their own secrets, so a CI binary is not what runs on the van.

## Why this is an ADR

Hard to reverse: `renewvan/<node>/info`, its field names and the manifest schema are a public wire contract that six Node repos, the relay, `node-demo` and the dashboard pin by name, and every Node repo's CI fetches the schema at a pinned ref. Surprising without context: a reader will wonder why the version is not in the manifest, why six repos carry a copy of the same small helper instead of importing it, why info stays retained after a Node dies, and why `node-demo` and the kiosk and Tailscale plugins have no manifest. The result of a real trade-off: a shared package and a manifest-held version would each be less work per Node, at the cost of coupled releases and disagreeing versions.

## See also

- `docs/authoring-a-node.md`: how to write a Node to this contract.
- `schema/node-manifest.schema.json`, `schema/node-info.schema.json`: the machine-checked contract and the Home Assistant mapping.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`: why each Node repo releases independently, and so why the helper is copied.
- `docs/adr/0008-node-naming-and-entity-neutrality.md`: a Node is named for its source; entities are named for what they are.
- `docs/adr/0010-demo-mode-publishes-under-renewvan-demo.md`: the Demo namespace that the reserved key `demo` protects.
- `CONTEXT.md`: `Node manifest`, `Node info`, `Node`, `Host` definitions.
