#!/usr/bin/env python3
"""Validates every demo scenario under schema/demo/ and enforces coverage.

Checks, per scenario file (`*.demo.json`):
  * the envelope against demo.schema.json;
  * `initial` against <entity>.schema.json;
  * every timeline step's `initial ⊕ set(0..i)` (cumulative overlay) against
    <entity>.schema.json, so a step can never leave the entity invalid;
  * `t` is non-decreasing and < `loop_s`.

Coverage rule (the per-node demo requirement): every entity that has a
<entity>.schema.json and appears in the demo envelope's `entity` enum must have
at least one scenario file. Adding an entity without a demo scenario fails.

Fleet pass: the simulated Nodes in demo/fleet.demo-fleet.json (not matched by the
`*.demo.json` glob above). Checks the envelope against demo-fleet.schema.json;
each entry's `info` against node-info.schema.json, with `info.node` equal to the
entry's `node`; the reserved key `demo` is rejected (by the slug pattern in
demo-fleet.schema.json and node-info.schema.json); every `publishes` type has an
entity scenario; `health_timeline` times are non-decreasing and < `loop_s`; and
exactly one Node carries a `health_timeline`.

Requires `jsonschema` (pip install jsonschema). Exit 1 on any failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).parent
DEMO_DIR = SCHEMA_DIR / "demo"
FLEET_PATH = DEMO_DIR / "fleet.demo-fleet.json"

# Schemas that are not bus entities (command/status topics, the envelope itself).
NON_ENTITY_PREFIXES = ("kiosk-", "tailscale-", "node-", "demo")


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


def validate_fleet(covered: set[str]) -> list[str]:
    """Fleet pass: returns failure messages (empty when the fleet is valid)."""
    rel = FLEET_PATH.relative_to(SCHEMA_DIR)
    if not FLEET_PATH.exists():
        return [f"{rel}: fleet file is missing"]
    fleet = load(FLEET_PATH)
    env_errors = list(
        Draft7Validator(load(SCHEMA_DIR / "demo-fleet.schema.json")).iter_errors(fleet)
    )
    if env_errors:
        return [f"{rel}: envelope: {env_errors[0].message}"]

    info_validator = Draft7Validator(load(SCHEMA_DIR / "node-info.schema.json"))
    failures: list[str] = []
    flapping: list[str] = []
    seen: set[str] = set()
    for entry in fleet["nodes"]:
        key = entry["node"]
        where = f"{rel}: node '{key}'"
        if key in seen:
            failures.append(f"{where}: duplicate node key")
        seen.add(key)

        info = entry.get("info")
        if info is not None:
            errs = list(info_validator.iter_errors(info))
            if errs:
                failures.append(f"{where}: info: {errs[0].message}")
            elif info["node"] != key:
                failures.append(f"{where}: info.node is '{info['node']}', expected '{key}'")
            else:
                for entity in info["publishes"]:
                    if entity not in covered:
                        failures.append(f"{where}: publishes '{entity}', which has no scenario")

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
        failures.append(f"{rel}: exactly one node must have a health_timeline, found: {found}")
    return failures


def main() -> int:
    failures: list[str] = []
    envelope = Draft7Validator(load(SCHEMA_DIR / "demo.schema.json"))
    schemas = {name: Draft7Validator(load(p)) for name, p in entity_schemas().items()}
    covered: set[str] = set()

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

    fleet_failures = validate_fleet(covered)
    if not fleet_failures:
        print(f"PASS  {FLEET_PATH.relative_to(SCHEMA_DIR)}")
    failures.extend(fleet_failures)

    for f in failures:
        print(f"FAIL  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
