"""Behavioural tests for schema/validate-demo.py: the fleet pass and the shipped relay scenarios.

The seam is the script's command line: it is run against a copy of schema/ in a temp
dir with one thing changed, and we assert on its exit status and what it reports.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).parent
FLEET = "demo/fleet.demo-fleet.json"


@pytest.fixture
def schema_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "schema"
    shutil.copytree(SCHEMA_DIR, dest, ignore=shutil.ignore_patterns("__pycache__", "examples"))
    return dest


def run_validator(schema: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(schema / "validate-demo.py")],
        capture_output=True,
        text=True,
        check=False,
    )


def edit_fleet(schema: Path, change) -> None:
    path = schema / FLEET
    fleet = json.loads(path.read_text())
    change(fleet)
    path.write_text(json.dumps(fleet))


def node(fleet: dict, key: str) -> dict:
    return next(n for n in fleet["nodes"] if n["node"] == key)


def test_shipped_fleet_passes(schema_copy: Path) -> None:
    result = run_validator(schema_copy)
    assert result.returncode == 0, result.stdout
    assert f"PASS  {FLEET}" in result.stdout


def test_fleet_file_is_not_read_as_an_entity_scenario(schema_copy: Path) -> None:
    # An entity scenario that is missing its `entity` key fails the envelope; the fleet
    # file has none, so if it were globbed as a scenario the run would fail on it.
    result = run_validator(schema_copy)
    assert "fleet.demo-fleet.json: envelope" not in result.stdout


def test_invalid_info_is_rejected(schema_copy: Path) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "tank")["info"].pop("version"))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "tank" in result.stdout and "version" in result.stdout


def test_publishes_type_without_entity_scenario_is_rejected(schema_copy: Path) -> None:
    for scenario in (schema_copy / "demo").glob("tilt.*.demo.json"):
        scenario.unlink()
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "publishes 'tilt'" in result.stdout


def test_reserved_key_demo_is_rejected(schema_copy: Path) -> None:
    def rename(fleet: dict) -> None:
        n = node(fleet, "tank")
        n["node"] = "demo"
        n["info"]["node"] = "demo"

    edit_fleet(schema_copy, rename)
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "FAIL  demo/fleet.demo-fleet.json" in result.stdout
    assert "'demo'" in result.stdout


def test_info_node_key_must_match_entry_key(schema_copy: Path) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "tank")["info"].update(node="tilt"))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "tank" in result.stdout


def test_timeline_time_at_or_past_loop_is_rejected(schema_copy: Path) -> None:
    def push_out_of_range(fleet: dict) -> None:
        flapper = next(n for n in fleet["nodes"] if n.get("health_timeline"))
        flapper["health_timeline"][-1]["t"] = fleet["loop_s"]

    edit_fleet(schema_copy, push_out_of_range)
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert ">= loop_s" in result.stdout


def test_unordered_timeline_is_rejected(schema_copy: Path) -> None:
    def reverse(fleet: dict) -> None:
        flapper = next(n for n in fleet["nodes"] if n.get("health_timeline"))
        flapper["health_timeline"].reverse()

    edit_fleet(schema_copy, reverse)
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "goes backwards" in result.stdout


def test_exactly_one_node_flaps(schema_copy: Path) -> None:
    def remove_flapper(fleet: dict) -> None:
        for n in fleet["nodes"]:
            n.pop("health_timeline", None)

    edit_fleet(schema_copy, remove_flapper)
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "exactly one" in result.stdout


def test_fleet_must_show_a_node_with_no_info(schema_copy: Path) -> None:
    edit_fleet(
        schema_copy, lambda f: node(f, "gps").update(info=node(f, "tank")["info"] | {"node": "gps"})
    )
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "no info" in result.stdout


def test_fleet_must_show_an_offline_node_with_retained_info(schema_copy: Path) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "tilt").update(health="online"))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "offline" in result.stdout


def test_fleet_must_show_a_dev_build(schema_copy: Path) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "tank")["info"].update(version="v0.0.0-demo"))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "dev" in result.stdout


def test_fleet_must_show_a_node_with_a_mac_serial(schema_copy: Path) -> None:
    edit_fleet(
        schema_copy,
        lambda f: node(f, "esp32-relay")["info"].update(host_serial="DEMO0000000000B2"),
    )
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "MAC" in result.stdout


def test_fleet_must_show_a_healthy_multi_entity_node(schema_copy: Path) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "venus")["info"].update(publishes=["battery"]))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "more than one entity" in result.stdout


def test_router_is_a_simulated_node(schema_copy: Path) -> None:
    def rename_router(fleet: dict) -> None:
        # Keep exactly one flapper so only the router rule can fire.
        router = node(fleet, "router")
        router["node"] = "uplink"
        router["info"]["node"] = "uplink"

    edit_fleet(schema_copy, rename_router)
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "the `router` node" in result.stdout


@pytest.mark.parametrize(
    "serial",
    ["10000000abcdef01", "00000000deadbeef"],
    ids=["real-looking-pi-serial", "pi-serial-not-marked-fake"],
)
def test_a_real_looking_pi_serial_is_rejected(schema_copy: Path, serial: str) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "venus")["info"].update(host_serial=serial))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "host_serial" in result.stdout


@pytest.mark.parametrize("version", ["v1.2.3", "0.8.0", "abc1234"])
def test_a_real_looking_version_is_rejected(schema_copy: Path, version: str) -> None:
    edit_fleet(schema_copy, lambda f: node(f, "venus")["info"].update(version=version))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "version" in result.stdout


def test_node_slug_pattern_must_not_drift_between_schemas(schema_copy: Path) -> None:
    path = schema_copy / "demo-fleet.schema.json"
    text = path.read_text().replace("(?!demo$)", "")
    path.write_text(text)
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert "pattern" in result.stdout


def relay_scenarios(schema: Path) -> dict[str, dict]:
    scenarios = [json.loads(p.read_text()) for p in (schema / "demo").glob("relay*.demo.json")]
    return {s["id"]: s for s in scenarios}


def test_demo_van_shows_named_and_unnamed_relays_and_one_that_switches(schema_copy: Path) -> None:
    scenarios = relay_scenarios(schema_copy)
    assert sorted(scenarios) == ["relay_01", "relay_02", "relay_03", "relay_04", "relay_05"]
    assert {i: s["initial"].get("name") for i, s in scenarios.items()} == {
        "relay_01": "Ceiling lights",
        "relay_02": "Reading lights",
        "relay_03": "Water pump",
        "relay_04": None,
        "relay_05": None,
    }
    switching = [i for i, s in scenarios.items() if s["timeline"]]
    assert switching == ["relay_02"]
    assert [(t["t"], t["set"]) for t in scenarios["relay_02"]["timeline"]] == [
        (30, {"state": True}),
        (90, {"state": False}),
    ]


def test_demo_relays_cover_on_and_off_at_rest(schema_copy: Path) -> None:
    scenarios = relay_scenarios(schema_copy)
    assert scenarios["relay_01"]["initial"]["state"] is True
    assert scenarios["relay_03"]["initial"]["state"] is False
    assert scenarios["relay_04"]["initial"]["state"] is True
    assert scenarios["relay_05"]["initial"]["state"] is False


def test_the_old_galley_relay_is_gone(schema_copy: Path) -> None:
    assert "galley" not in relay_scenarios(schema_copy)


def test_a_relay_scenario_that_blanks_the_name_is_rejected(schema_copy: Path) -> None:
    path = next(
        p
        for p in (schema_copy / "demo").glob("relay*.demo.json")
        if json.loads(p.read_text())["id"] == "relay_02"
    )
    scenario = json.loads(path.read_text())
    scenario["timeline"].append({"t": 100, "set": {"name": ""}})
    path.write_text(json.dumps(scenario))
    result = run_validator(schema_copy)
    assert result.returncode == 1
    assert path.name in result.stdout
