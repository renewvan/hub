# Authoring a Driver

How to make a new **Driver** (see `Driver` in `CONTEXT.md`) describe itself on the renewvan bus, so the dashboard's Settings › Drivers and Firmware pages can list it. The decision and its rejected alternatives are in [ADR 0011](adr/0011-node-manifest-and-retained-info-topic.md). For the sensing side of a new Driver, see [porting-dbus-to-mqtt-driver.md](porting-dbus-to-mqtt-driver.md).

**Shortcut:** the `/scaffold-driver` skill bootstraps a new `renewvan/driver-<entity>` repo (MQTT skeleton, layered config, systemd unit, Dockerfile, CI) and runs a design round for its topics and hardware interface. See [agent-skills.md](agent-skills.md).

This applies to Drivers only. Host-level plugins (`plugins/kiosk`, `plugins/tailscale`) and `driver-demo` are not Drivers and have no manifest or info.

A Driver does three things: ships a `manifest.json`, publishes **Driver info** built from it, and passes the CI gate. The contract is two schemas in this repo: [`driver-manifest.schema.json`](../schema/driver-manifest.schema.json) and [`driver-info.schema.json`](../schema/driver-info.schema.json). If this guide and a schema disagree, the schema wins.

## 1. Write `manifest.json`

At the root of your Driver repo. It is the single source of the static fields.

```json
{
  "driver": "venus",
  "product": "Venus OS bridge",
  "source": "Victron Venus OS MQTT feed",
  "publishes": ["battery", "solar", "charger"],
  "manufacturer": "renewvan",
  "homepage": "https://github.com/renewvan/driver-venus"
}
```

| Field          | Required | Rule                                                                                                                                                                                                                                                                                                                                                                            |
| -------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `driver`       | yes      | Lowercase slug starting with a letter (letters, digits, single hyphens), no `driver-` prefix (repo `driver-venus` is driver `venus`; repo `driver-esp32` is the one Driver `esp32`; its firmware variants, such as `esp32-16x-relay`, only select the firmware and are not keys, ADR 0020). Must equal the topic segment you publish under. `demo` is reserved and is rejected. |
| `product`      | yes      | Human name, non-empty. Shown as the Driver's title.                                                                                                                                                                                                                                                                                                                             |
| `source`       | yes      | What you read: the hardware or feed. Non-empty.                                                                                                                                                                                                                                                                                                                                 |
| `publishes`    | yes      | Non-empty, unique list of entity types from the `publishes` enum in the schema (one value per entity schema under `schema/`).                                                                                                                                                                                                                                                   |
| `manufacturer` | no       | Non-empty when present. Consumers show `renewvan` when info exists without one.                                                                                                                                                                                                                                                                                                 |
| `homepage`     | no       | An `http://` or `https://` URL.                                                                                                                                                                                                                                                                                                                                                 |

**`version` and `board_serial` are forbidden in the manifest** (the schema rejects them). A hand-edited version could disagree with the release; the serial belongs to whichever Host the Driver lands on.

A Driver is named for its source, not the entity it publishes (ADR 0008), and `publishes` lists every entity type it publishes. An entity type that is not in the enum has no schema yet: add the entity first (see "When you add an entity" below).

## 2. Stamp the version

The version is not in the manifest. Your Driver reads it at runtime.

- **Python Drivers:** the git **release tag** is the only source. The release workflow passes it as a build argument that becomes the `DRIVER_VERSION` environment variable the Driver reads (the release workflow's `driver-version-arg` input). Do not keep a `version` file or a `__version__`; the packaging version is a fixed placeholder nothing reads.
- **`dev` fallback:** when the variable is unset, report the literal `dev`. A development run still publishes something truthful, and `version` is required in info, so never publish an empty one.
- **ESP32 relay:** the owner flashes a locally built binary, so a CI binary is not what runs on a van. The flash script derives the version from the owner's checkout with `git describe --tags --always --dirty` and always passes it to the build. An untagged tree flashes with a hash version; the script does not refuse. The firmware defaults to `dev`, because a missing build argument would otherwise silently ship a literal placeholder.

## 3. Publish info before `online`

Two retained topics, both three segments (so neither looks like an entity topic and the logger ignores both):

| Topic                      | Payload                                                                                           |
| -------------------------- | ------------------------------------------------------------------------------------------------- |
| `renewvan/<driver>/info`   | Driver info JSON (manifest fields + `version` and, when known, `board_serial` and `ip`), retained |
| `renewvan/<driver>/health` | bare word `online` or `offline`, retained, with a last will of `offline`                          |

Rules:

- **On every connect, publish info first, then `online`.** A consumer that sees `online` then always finds info already retained, and republishing on reconnect heals a broker that lost its retained state.
- **Leave info retained when you go offline.** Do not clear it on shutdown or from the last will. That retained info is what lets a consumer say "offline, last seen vX".
- Info validates against `driver-info.schema.json`: the manifest fields, a required non-empty `version`, an optional non-empty `board_serial`, and an optional `ip`, with no other properties.
- **Name yourself on every entity instance.** Publish `renewvan/<entity>/<id>/driver` (retained, a JSON string equal to your key, the `<driver>` segment above) at the same moment as the instance's other retained identity fields: on startup, on every (re)connect, and when a dynamic instance is first discovered. It is optional on the wire and a consumer tolerates it being absent (it then attributes the instance to every Driver whose info `publishes` that entity), but it is how two Drivers publishing the same entity stay distinguishable. Optionally publish a human label as `renewvan/<entity>/<id>/name` (retained, non-empty string). See `docs/adr/0019-entity-instances-name-their-driver-and-shared-primary-battery.md`.

Python Drivers copy one small helper into their own repo (about 15 lines) rather than importing a shared package, so each Driver releases independently (ADR 0011, ADR 0001). It loads the manifest, adds the stamped version and the serial, and serialises the result. The decided layout for Python Drivers is that the image build copies `manifest.json` into the package and the Driver reads the copy beside its own module, falling back to the repo-root file when run from a checkout. No Driver implements this yet.

## 4. Host serial: parse and omit

`board_serial` is a support identity for display, never a join key. Every Driver on one Pi publishes the same value.

- **On a Pi** (in a container or not): the `Serial` line of the host's `/proc/cpuinfo`. A default container can already read it, so no compose or Dockerfile change and no extra mount is needed. Read it once at process start.
- **ESP32:** the MAC address as `AA:BB:CC:DD:EE:FF`.
- **Omit the field** (do not send `unknown` or a placeholder) when it cannot be read, or when it is empty or all zeros. An all-zero serial would make every Pi with a failing kernel look identical.

The schema only requires a non-empty string, so these two formats are conventions the schema description records, not something it enforces.

**`ip` is optional and only for a Driver that has its own network address**, such as an ESP32 on WiFi: the IPv4 address DHCP gave the Board, in dotted-quad form. The schema enforces the format and rejects `0.0.0.0`. Read it when you publish info, which you do on every connect, so it is as fresh as the Driver's last (re)connect; a Driver that is not told its address changed does not republish until it reconnects. Omit it, never send `0.0.0.0` or a placeholder, when the Board has no address yet. A Driver on the Pi does not publish it: the Pi's address is on `renewvan/hub/status` (the hub plugin), and every Driver on one Pi would only repeat it.

## 5. Pass the CI gate

Every Driver repo's CI fetches `driver-manifest.schema.json` from this repo at a **pinned ref** and validates `manifest.json` against it. Pinning means a later hub change cannot break your CI unannounced; bump the pin when you want a newer contract.

To check locally against this repo's copy:

```sh
npx --yes ajv-cli@5 validate -s path/to/hub/schema/driver-manifest.schema.json -d manifest.json
```

## 6. Demo fleet

A Driver is not done for the dashboard until Demo mode can show it. The simulated fleet is declared in one hub-owned file, `schema/demo/fleet.demo-fleet.json`, that `driver-demo` replays under the Demo namespace as retained info and health. `python3 schema/validate-demo.py` checks each entry's info against `driver-info.schema.json`, and that every type in its `publishes` has an entity scenario under `schema/demo/`. Versions and serials in the fleet are visibly fake and never read from the Pi. See [demo-mode.md](demo-mode.md).

Add your Driver to the fleet in the same change as its first release if you want Demo mode to show it. A new **required** property on the info schema needs every fleet entry updated in the same change. The fleet format is in [demo-mode.md](demo-mode.md#simulated-drivers-the-fleet-file).

## When you add an entity

A new `schema/<entity>.schema.json` must also be added to the `publishes` enum in **both** `driver-manifest.schema.json` and `driver-info.schema.json`. `schema/validate-examples.sh` fails when an entity schema exists that the enum does not list, and when the enum lists one that has no schema. A new entity also needs a demo scenario ([demo-mode.md](demo-mode.md)).

## Checklist

- [ ] `manifest.json` at the repo root validates against `driver-manifest.schema.json`, with no `version` or `board_serial`.
- [ ] `driver` is a lowercase slug, no `driver-` prefix, not `demo`, and equals the topic segment (a repo is one Driver with one manifest, including `driver-esp32`, whose variants only select firmware).
- [ ] Version comes from the release tag via an environment variable, or is `dev`.
- [ ] On every connect: info published retained, then `online` retained; last will is `offline`.
- [ ] Info is never cleared on offline.
- [ ] `board_serial` omitted when unreadable, empty, or all zeros.
- [ ] CI validates the manifest against the hub schema at a pinned ref.
