# Automating hub work with agent skills

Several recurring hub tasks can be driven by [Claude Code](https://claude.com/claude-code) skills. A skill is a packaged set of instructions the agent follows for one kind of task. You invoke it by typing its name as a slash command, for example `/scaffold-driver`, or by describing the task and letting the agent pick it.

Skills are optional. Every task below can also be done by hand with the linked doc.

## Skills for hub tasks

| Skill              | Use it to                                                                                                                                                                                  | Manual equivalent                                                |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `/scaffold-driver` | Bootstrap a new `renewvan/driver-<entity>` repo (MQTT skeleton, layered config, systemd unit, Dockerfile, CI) from the `driver-tank` shape, then design its topics and hardware interface. | [Authoring a driver](authoring-a-driver.md)                      |
| `/build-basemap`   | Build the offline PMTiles basemap for a region, city or country, verify it covers a point, and optionally push it to the Pi.                                                               | [Offline map](reference.md#offline-map)                          |
| `/deploy`          | Deploy the hub with `bin/deploy.sh`: pre-flight checks (Pi reachable, uncommitted changes), the run, and verification afterwards.                                                          | [Deploying to a Pi](reference.md#deploying-to-a-pi-step-by-step) |

`/deploy` lives in this repo under `.claude/skills/deploy` and `.agents/skills/deploy`. `/scaffold-driver` and `/build-basemap` are installed in your own Claude Code setup, not shipped in this repo.

## Porting a D-Bus driver to native MQTT

There is no single "port" skill. The port is a recipe ([porting-dbus-to-mqtt-driver.md](porting-dbus-to-mqtt-driver.md)), and you chain skills along its steps:

| Step                                                   | Skill                                                                                             |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| 1. Settle the entity, topics and vocabulary            | `domain-modeling` (also records an ADR), `grilling` to stress-test the design                     |
| 2. Create the repo skeleton                            | `/scaffold-driver`                                                                                |
| 3. Port the sensing logic test-first                   | `tdd`                                                                                             |
| 4. Write `manifest.json` and Driver info               | follow [Authoring a driver](authoring-a-driver.md); validate with `./schema/validate-examples.sh` |
| 5. Add the entity schema, examples and a demo scenario | edit `schema/`, then run both validators ([Validating schemas](reference.md#validating-schemas))  |
| 6. Chase failures                                      | `diagnosing-bugs`                                                                                 |
| 7. Review before merging                               | `code-review`                                                                                     |
| 8. Pin the image in compose and ship                   | edit the `image:` tag in `docker-compose.yml`, then `/deploy`                                     |

Start with `/scaffold-driver` and tell it which D-Bus package you are porting (`dbus-ads1115`, `dbus_wattcycle_ble`, `dbus-autoterm`, `dbus-giandel-bridge`). Its design round is where the topics and hardware interface get decided, so have the recipe open.

## Other useful skills

These are general-purpose skills from the Claude Code plugins used on this project. Availability depends on your setup.

| Task                                            | Skill             |
| ----------------------------------------------- | ----------------- |
| Build a feature or fix a bug test-first         | `tdd`             |
| Diagnose something broken, throwing or slow     | `diagnosing-bugs` |
| Review a branch or PR against repo standards    | `code-review`     |
| Sharpen terminology or record an ADR            | `domain-modeling` |
| Stress-test a plan before building              | `grilling`        |
| Throwaway prototype to answer a design question | `prototype`       |
| Sync docs from source-of-truth files            | `update-docs`     |

## Conventions skills must respect

Skills operate under the repo's `AGENTS.md`:

- Services ship as pinned images; do not add a Dockerfile or local build to the hub.
- Run `./schema/validate-examples.sh` after any schema change, and `python3 schema/validate-demo.py` when an entity gains a schema or a required property.
- A new entity needs a demo scenario in the same change.
- Commits go through the pre-commit formatter and linter.
