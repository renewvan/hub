"""Behavioural tests for the fleet pass of schema/validate-demo.py.

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


def test_fleet_covers_every_device_page_state(schema_copy: Path) -> None:
    fleet = json.loads((schema_copy / FLEET).read_text())
    nodes = fleet["nodes"]
    infos = [n["info"] for n in nodes if "info" in n]

    assert any(len(i["publishes"]) > 1 and i["version"] != "dev" for i in infos), "healthy multi"
    assert any(i["version"] == "dev" for i in infos), "dev build"
    assert any("info" not in n and n["health"] == "online" for n in nodes), "no info"
    assert any("info" in n and n["health"] == "offline" for n in nodes), "offline with info"
    assert any(len(i.get("host_serial", "").split(":")) == 6 for i in infos), (
        "ESP32 node with a MAC serial"
    )
    assert any(n["node"] == "router" for n in nodes), "router is a simulated node"


def test_no_real_pi_serial_or_release_version_appears(schema_copy: Path) -> None:
    fleet = json.loads((schema_copy / FLEET).read_text())
    for n in fleet["nodes"]:
        info = n.get("info", {})
        serial = info.get("host_serial", "")
        assert serial == "" or serial.startswith("DEMO") or ":" in serial
        assert info.get("version", "dev") in ("dev",) or "demo" in info["version"].lower()
