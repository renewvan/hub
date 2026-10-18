# Authoring a Node

How to make a new **Node** (see `Node` in `CONTEXT.md`) describe itself on the renewvan bus, so the dashboard's Settings › Nodes and Firmware pages can list it. The decision and its rejected alternatives are in [ADR 0011](adr/0011-node-manifest-and-retained-info-topic.md). For the sensing side of a new Node, see [porting-dbus-to-mqtt-node.md](porting-dbus-to-mqtt-node.md).

This applies to Nodes only. Host-level plugins (`plugins/kiosk`, `plugins/tailscale`) and `node-demo` are not Nodes and have no manifest or info.

A Node does three things: ships a `manifest.json`, publishes **Node info** built from it, and passes the CI gate. The contract is two schemas in this repo: [`node-manifest.schema.json`](../schema/node-manifest.schema.json) and [`node-info.schema.json`](../schema/node-info.schema.json). If this guide and a schema disagree, the schema wins.

## 1. Write `manifest.json`

At the root of your Node repo. It is the single source of the static fields.

```json
{
  "node": "venus",
  "product": "Venus OS bridge",
  "source": "Victron Venus OS MQTT feed",
  "publishes": ["battery", "solar", "charger"],
  "manufacturer": "renewvan",
  "homepage": "https://github.com/renewvan/node-venus"
}
```

| Field          | Required | Rule                                                                                                                                                       |
| -------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `node`         | yes      | Lowercase slug, no `node-` prefix (repo `node-venus` is node `venus`). Must equal the topic segment you publish under. `demo` is reserved and is rejected. |
| `product`      | yes      | Human name, non-empty. Shown as the Node's title.                                                                                                          |
| `source`       | yes      | What you read: the hardware or feed. Non-empty.                                                                                                            |
| `publishes`    | yes      | Non-empty, unique list of entity types from the schema's enum (`battery`, `charger`, `gps`, `relay`, `router`, `solar`, `tank`, `temperature`, `tilt`).    |
| `manufacturer` | no       | Non-empty when present. Consumers show `renewvan` when info exists without one.                                                                            |
| `homepage`     | no       | An `http://` or `https://` URL.                                                                                                                            |

**`version` and `host_serial` are forbidden in the manifest** (the schema rejects them). A hand-edited version could disagree with the release; the serial belongs to whichever Host the Node lands on.

A Node is named for its source, not the entity it publishes (ADR 0008), and `publishes` lists every entity type it publishes. An entity type that is not in the enum has no schema yet: add the entity first (see "When you add an entity" below).

## 2. Stamp the version

The version is not in the manifest. Your Node reads it at runtime.

- **Python Nodes:** the git **release tag** is the only source. The release workflow passes it as a build argument that becomes an environment variable the Node reads. Do not keep a `version` file or a `__version__`; the packaging version is a fixed placeholder nothing reads.
- **`dev` fallback:** when the variable is unset, report the literal `dev`. A development run still publishes something truthful, and `version` is required in info, so never publish an empty one.
- **ESP32 relay:** the owner flashes a locally built binary, so a CI binary is not what runs on a van. The flash script derives the version from the owner's checkout with `git describe --tags --always --dirty` and always passes it to the build. An untagged tree flashes with a hash version; the script does not refuse. The firmware defaults to `dev`.

## 3. Publish info before `online`

Two retained topics, both three segments (so neither looks like an entity topic and the logger ignores both):

| Topic                    | Payload                                                                               |
| ------------------------ | ------------------------------------------------------------------------------------- |
| `renewvan/<node>/info`   | Node info JSON (manifest fields + `version` and, when known, `host_serial`), retained |
| `renewvan/<node>/health` | bare word `online` or `offline`, retained, with a last will of `offline`              |

Rules:

- **On every connect, publish info first, then `online`.** A consumer that sees `online` then always finds info already retained, and republishing on reconnect heals a broker that lost its retained state.
- **Leave info retained when you go offline.** Do not clear it on shutdown or from the last will. That retained info is what lets a consumer say "offline, last seen vX".
- Info validates against `node-info.schema.json`: the manifest fields, a required non-empty `version`, and an optional non-empty `host_serial`, with no other properties.

Python Nodes copy one small helper into their own repo (about 15 lines) rather than importing a shared package, so each Node releases independently (ADR 0011, ADR 0001). It loads the manifest, adds the stamped version and the serial, and serialises the result. Python Node images copy `manifest.json` into the package at build time and the Node reads the copy beside its own module, falling back to the repo-root file when run from a checkout.

## 4. Host serial: parse and omit

`host_serial` is a support identity for display, never a join key. Every Node on one Pi publishes the same value.

- **On a Pi** (in a container or not): the `Serial` line of the host's `/proc/cpuinfo`. A default container can already read it, so no compose or Dockerfile change and no extra mount is needed. Read it once at process start.
- **ESP32:** the MAC address as `AA:BB:CC:DD:EE:FF`.
- **Omit the field** (do not send `unknown` or a placeholder) when it cannot be read, or when it is empty or all zeros. An all-zero serial would make every Pi with a failing kernel look identical.

The schema only requires a non-empty string, so these two formats are conventions the schema description records, not something it enforces.

## 5. Pass the CI gate

Every Node repo's CI fetches `node-manifest.schema.json` from this repo at a **pinned ref** and validates `manifest.json` against it. Pin to a tag or commit SHA, not a branch, so a hub change cannot break your CI unannounced; bump the pin when you want a newer contract.

To check locally against this repo's copy:

```sh
npx --yes ajv-cli@5 validate -s path/to/hub/schema/node-manifest.schema.json -d manifest.json
```

## 6. Demo fleet

A Node is not done for the dashboard until Demo mode can show it. The simulated fleet is declared in one hub-owned file that `node-demo` replays under the Demo namespace as retained info and health. Each simulated Node's info must validate against `node-info.schema.json`, and every type in its `publishes` must have an entity scenario under `schema/demo/`. Versions and serials in the fleet are visibly fake and never read from the Pi. See [demo-mode.md](demo-mode.md).

> The fleet file and its validator are specified in renewvan/hub#35 but are not in this repo yet. Until they land, a new Node ships no fleet entry; this section describes the requirement it will be held to.

## When you add an entity

A new `schema/<entity>.schema.json` must also be added to the `publishes` enum in **both** `node-manifest.schema.json` and `node-info.schema.json`. `schema/validate-examples.sh` fails when an entity schema exists that the enum does not list, and when the enum lists one that has no schema. A new entity also needs a demo scenario ([demo-mode.md](demo-mode.md)).

## Checklist

- [ ] `manifest.json` at the repo root validates against `node-manifest.schema.json`, with no `version` or `host_serial`.
- [ ] `node` is a lowercase slug, no `node-` prefix, not `demo`, and equals the topic segment.
- [ ] Version comes from the release tag via an environment variable, or is `dev`.
- [ ] On every connect: info published retained, then `online` retained; last will is `offline`.
- [ ] Info is never cleared on offline.
- [ ] `host_serial` omitted when unreadable, empty, or all zeros.
- [ ] CI validates the manifest against the hub schema at a pinned ref.
