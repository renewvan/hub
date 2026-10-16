#!/usr/bin/env python3
"""Validates every demo scenario under schema/demo/ and enforces coverage.

Checks, per scenario file:
  * the envelope against demo.schema.json;
  * `initial` against <entity>.schema.json;
  * every timeline step's `initial ⊕ set(0..i)` (cumulative overlay) against
    <entity>.schema.json, so a step can never leave the entity invalid;
  * `t` is non-decreasing and < `loop_s`.

Coverage rule (the per-node demo requirement): every entity that has a
<entity>.schema.json and appears in the demo envelope's `entity` enum must have
at least one scenario file. Adding an entity without a demo scenario fails.

Requires `jsonschema` (pip install jsonschema). Exit 1 on any failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).parent
DEMO_DIR = SCHEMA_DIR / "demo"

# Schemas that are not bus entities (command/status topics, the envelope itself).
NON_ENTITY_PREFIXES = ("kiosk-", "tailscale-", "demo")


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

    for f in failures:
        print(f"FAIL  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
