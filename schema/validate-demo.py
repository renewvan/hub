#!/usr/bin/env python3
"""Validates every demo scenario under schema/demo/ and enforces coverage.

Checks, per scenario file (`*.demo.json`):
  * the envelope against demo.schema.json;
  * `initial` against <entity>.schema.json;
  * every timeline step's `initial ⊕ set(0..i)` (cumulative overlay) against
    <entity>.schema.json, so a step can never leave the entity invalid;
  * `t` is non-decreasing and < `loop_s`.

Coverage rule (the per-driver demo requirement): every entity that has a
<entity>.schema.json and appears in the demo envelope's `entity` enum must have
at least one scenario file. Adding an entity without a demo scenario fails.

Fleet pass: the simulated Drivers in demo/fleet.demo-fleet.json (not matched by the
`*.demo.json` glob above). Checks the envelope against demo-fleet.schema.json;
each entry's `info` against driver-info.schema.json, with `info.driver` equal to the
entry's `driver`; the reserved key `demo` is rejected (by the slug pattern in
demo-fleet.schema.json and driver-info.schema.json); every `publishes` type has an
entity scenario; `health_timeline` times are non-decreasing and < `loop_s`; exactly
one Driver carries a `health_timeline`; versions and serials are visibly fake (`dev` or
`*-demo`; a `DEMO` serial or a unicast, locally administered MAC; an `ip` in an RFC 5737
documentation range); a scenario whose `initial` names a `driver` names a fleet driver whose
info `publishes` that entity; the driver-key pattern matches
driver-info.schema.json's so the reserved key cannot drift; and the fleet shows every
Driver-page state (a driver with no info, an offline driver with retained info, a `dev`
build, a MAC serial, an IP address, a multi-entity driver) plus the `router`.

Requires `jsonschema` (pip install jsonschema). Exit 1 on any failure.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).parent
DEMO_DIR = SCHEMA_DIR / "demo"
FLEET_PATH = DEMO_DIR / "fleet.demo-fleet.json"

# Schemas that are not bus entities (command/status topics, the envelope itself).
NON_ENTITY_PREFIXES = ("kiosk-", "tailscale-", "hub-", "driver-", "demo")


def load(path: Path):
    return json.loads(path.read_text())


def entity_schemas() -> dict[str, Path]:
    found = {}
    for p in sorted(SCHEMA_DIR.glob("*.schema.json")):
        name = p.name.removesuffix(".schema.json")
        if name.startswith(NON_ENTITY_PREFIXES):
            continue
        found[name] = p
    return found


# A visibly fake board serial is a `DEMO` marker, or a locally administered MAC (second hex digit
# of the first octet is 2, 6, A or E, so unicast): vendors assign globally administered MACs.
FAKE_PI_SERIAL = re.compile(r"^DEMO[0-9A-F]{12}$")
MAC_SERIAL = re.compile(r"^[0-9A-F][26AE](:[0-9A-F]{2}){5}$")
FAKE_VERSION = re.compile(r"^(dev|.*-demo)$")
# A visibly fake IP is one of the three RFC 5737 documentation ranges: never routable, so never a
# real van's address.
FAKE_IP = re.compile(r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.\d{1,3}$")


def validate_fleet(covered: set[str], attributed: list[tuple[str, str, str]]) -> list[str]:
    """Fleet pass: returns failure messages (empty when the fleet is valid).

    `attributed` is every (scenario file, entity, driver) triple from a scenario whose `initial`
    names its Driver.
    """
    rel = FLEET_PATH.relative_to(SCHEMA_DIR)
    if not FLEET_PATH.exists():
        return [f"{rel}: fleet file is missing"]
    fleet = load(FLEET_PATH)
    fleet_schema = load(SCHEMA_DIR / "demo-fleet.schema.json")
    info_schema = load(SCHEMA_DIR / "driver-info.schema.json")
    env_errors = list(Draft7Validator(fleet_schema).iter_errors(fleet))
    if env_errors:
        return [f"{rel}: envelope: {env_errors[0].message}"]

    # The reserved key `demo` is rejected by the driver-key pattern in each schema; fail if the
    # fleet's copy ever stops matching the real contract's.
    fleet_pattern = fleet_schema["properties"]["drivers"]["items"]["properties"]["driver"][
        "pattern"
    ]
    if fleet_pattern != info_schema["properties"]["driver"]["pattern"]:
        return [f"{rel}: driver key pattern differs from driver-info.schema.json"]

    info_validator = Draft7Validator(info_schema)
    failures: list[str] = []
    flapping: list[str] = []
    seen: set[str] = set()
    valid_infos: list[dict] = []
    for entry in fleet["drivers"]:
        key = entry["driver"]
        where = f"{rel}: driver '{key}'"
        if key in seen:
            failures.append(f"{where}: duplicate driver key")
        seen.add(key)

        info = entry.get("info")
        if info is not None:
            errs = list(info_validator.iter_errors(info))
            if errs:
                failures.append(f"{where}: info: {errs[0].message}")
            elif info["driver"] != key:
                failures.append(f"{where}: info.driver is '{info['driver']}', expected '{key}'")
            else:
                valid_infos.append(info)
                for entity in info["publishes"]:
                    if entity not in covered:
                        failures.append(f"{where}: publishes '{entity}', which has no scenario")
                if not FAKE_VERSION.match(info["version"]):
                    failures.append(
                        f"{where}: version '{info['version']}' must be `dev` or end -demo"
                    )
                serial = info.get("board_serial")
                if serial is not None and not (
                    FAKE_PI_SERIAL.match(serial) or MAC_SERIAL.match(serial)
                ):
                    failures.append(f"{where}: board_serial '{serial}' is not visibly fake")
                address = info.get("ip")
                if address is not None and not FAKE_IP.match(address):
                    failures.append(
                        f"{where}: ip '{address}' must be in an RFC 5737 documentation range"
                    )

        timeline = entry.get("health_timeline")
        if timeline:
            flapping.append(key)
            last_t = -1
            for i, step in enumerate(timeline):
                t = step["t"]
                if t < last_t:
                    failures.append(f"{where}: health_timeline[{i}].t={t} goes backwards")
                if t >= fleet["loop_s"]:
                    failures.append(
                        f"{where}: health_timeline[{i}].t={t} >= loop_s={fleet['loop_s']}"
                    )
                last_t = t

    if len(flapping) != 1:
        found = ", ".join(flapping) or "none"
        failures.append(f"{rel}: exactly one driver must have a health_timeline, found: {found}")

    wanted = [
        (any("info" not in e for e in fleet["drivers"]), "a driver with no info"),
        (
            any(e["health"] == "offline" and "info" in e for e in fleet["drivers"]),
            "an offline driver with retained info",
        ),
        (any(i["version"] == "dev" for i in valid_infos), "a `dev` build"),
        (
            any(MAC_SERIAL.match(i.get("board_serial", "")) for i in valid_infos),
            "a driver with a MAC serial",
        ),
        (
            any("ip" in i for i in valid_infos),
            "a driver with an IP address",
        ),
        (
            any(len(i["publishes"]) > 1 for i in valid_infos),
            "a driver publishing more than one entity",
        ),
        ("router" in seen, "the `router` driver"),
    ]
    failures.extend(f"{rel}: fleet must include {what}" for ok, what in wanted if not ok)

    publishes = {i["driver"]: set(i["publishes"]) for i in valid_infos}
    for scenario_rel, entity, owner in attributed:
        if entity not in publishes.get(owner, set()):
            failures.append(
                f"{scenario_rel}: driver '{owner}' is not a fleet driver that publishes '{entity}'"
            )
    return failures


def main() -> int:
    failures: list[str] = []
    envelope = Draft7Validator(load(SCHEMA_DIR / "demo.schema.json"))
    schemas = {name: Draft7Validator(load(p)) for name, p in entity_schemas().items()}
    covered: set[str] = set()
    attributed: list[tuple[str, str, str]] = []

    for path in sorted(DEMO_DIR.glob("*.demo.json")):
        rel = path.relative_to(SCHEMA_DIR)
        scenario = load(path)
        env_errors = list(envelope.iter_errors(scenario))
        if env_errors:
            failures.append(f"{rel}: envelope: {env_errors[0].message}")
            continue
        entity = scenario["entity"]
        if entity not in schemas:
            failures.append(f"{rel}: no {entity}.schema.json to validate against")
            continue
        covered.add(entity)
        if "driver" in scenario["initial"]:
            attributed.append((str(rel), entity, scenario["initial"]["driver"]))
        validator = schemas[entity]

        state = dict(scenario["initial"])
        errs = list(validator.iter_errors(state))
        if errs:
            failures.append(f"{rel}: initial: {errs[0].message}")
        last_t = -1
        for i, step in enumerate(scenario["timeline"]):
            t = step["t"]
            if t < last_t:
                failures.append(f"{rel}: timeline[{i}].t={t} goes backwards")
            if t >= scenario["loop_s"]:
                failures.append(f"{rel}: timeline[{i}].t={t} >= loop_s={scenario['loop_s']}")
            last_t = t
            state.update(step["set"])
            errs = list(validator.iter_errors(state))
            if errs:
                failures.append(f"{rel}: timeline[{i}] (t={t}): {errs[0].message}")
        if not failures or not any(f.startswith(str(rel)) for f in failures):
            print(f"PASS  {rel}")

    for entity in sorted(set(schemas) - covered):
        failures.append(f"entity '{entity}' has no demo scenario under schema/demo/")

    fleet_failures = validate_fleet(covered, attributed)
    if not fleet_failures:
        print(f"PASS  {FLEET_PATH.relative_to(SCHEMA_DIR)}")
    failures.extend(fleet_failures)

    for f in failures:
        print(f"FAIL  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
