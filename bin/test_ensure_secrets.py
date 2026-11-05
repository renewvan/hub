"""Tests for bin/ensure-secrets.sh, run against a fake `docker` that records its calls."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("ensure-secrets.sh")

SECRET_NAMES = (
    "INFLUXDB_ADMIN_PASSWORD",
    "INFLUXDB_ADMIN_TOKEN",
    "INFLUXDB_LOGGER_TOKEN",
    "INFLUXDB_GRAFANA_TOKEN",
    "INFLUXDB_DASHBOARD_TOKEN",
    "GF_SECURITY_ADMIN_PASSWORD",
)

FAKE_DOCKER = r"""#!/usr/bin/env bash
state="$FAKE_DOCKER_STATE"
echo "$*" >>"$state/calls.log"
case "$1" in
  inspect) [[ -e "$state/exists_$2" ]] ;;
  compose) touch "$state/started" ;;
  exec)
    shift
    while [[ "$1" == -e ]]; do shift 2; done
    shift  # container name
    if [[ "$1" == influx ]]; then
      shift
      case "$1 $2" in
        "ping "*) [[ -e "$state/started" || -e "$state/exists_renewvan-influxdb" ]] ;;
        "bucket list") echo "bkt123 renewvan 0s" ;;
        "auth create")
          n=$(($(cat "$state/n" 2>/dev/null || echo 0) + 1)); echo "$n" >"$state/n"
          echo "{\"id\":\"id$n\",\"token\":\"tok$n\"}" ;;
        "auth list") printf 'ID1\tdesc\trenewvan-dev-token\tadmin\n' ;;
      esac
    fi ;;
esac
exit $?
"""


class EnsureSecretsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        bindir = self.dir / "bin"
        bindir.mkdir()
        docker = bindir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        curl = bindir / "curl"  # stands in for Grafana's /api/health
        curl.write_text("#!/bin/sh\nexit 0\n")
        curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
        self.env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "FAKE_DOCKER_STATE": str(self.state),
            "ENSURE_SECRETS_POLL": "0",
        }
        self.envfile = self.dir / ".env"

    def write_env(self, **values):
        lines = [f"{k}={values.get(k, '')}" for k in SECRET_NAMES]
        lines.append("DASHBOARD_HOST=10.0.0.5")
        self.envfile.write_text("\n".join(lines) + "\n")

    def exists(self, *containers):
        for c in containers:
            (self.state / f"exists_{c}").touch()

    def run_script(self):
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.envfile)],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.dir,
        )

    def values(self):
        out = {}
        for line in self.envfile.read_text().splitlines():
            k, _, v = line.partition("=")
            out[k] = v
        return out

    def calls(self):
        log = self.state / "calls.log"
        return log.read_text() if log.exists() else ""

    def test_fresh_install_generates_everything(self):
        self.write_env()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        v = self.values()
        for name in SECRET_NAMES:
            self.assertNotIn(
                v[name], ("", "pending", "renewvan-dev-token", "renewvan-dev-only"), name
            )
        self.assertEqual(
            len({v[n] for n in SECRET_NAMES}), len(SECRET_NAMES), "secrets must be distinct"
        )
        self.assertEqual(v["DASHBOARD_HOST"], "10.0.0.5", "unrelated keys survive")
        self.assertEqual(stat.S_IMODE(self.envfile.stat().st_mode), 0o600)
        self.assertNotIn(
            "reset-admin-password", self.calls(), "a fresh Grafana volume takes the env var"
        )
        self.assertNotIn("user password", self.calls())
        self.assertIn("--write-bucket bkt123", self.calls())
        self.assertIn("--read-bucket bkt123", self.calls())
        for name in SECRET_NAMES:
            self.assertIn(name, r.stdout)
            self.assertNotIn(v[name], r.stdout + r.stderr, "values are never printed")

    def test_upgrade_from_example_values_rotates_in_place(self):
        self.write_env(
            INFLUXDB_ADMIN_PASSWORD="renewvan-dev-only",
            INFLUXDB_ADMIN_TOKEN="renewvan-dev-token",
            GF_SECURITY_ADMIN_PASSWORD="admin",
        )
        self.exists("renewvan-influxdb", "renewvan-grafana")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertIn("auth delete --id ID1", calls, "the public admin token is revoked")
        self.assertIn("user password --name admin", calls)
        self.assertIn("grafana cli admin reset-admin-password", calls)
        v = self.values()
        self.assertEqual(v["INFLUXDB_ADMIN_TOKEN"], "tok1", "uses the value InfluxDB created")
        self.assertNotEqual(v["INFLUXDB_ADMIN_PASSWORD"], "renewvan-dev-only")
        self.assertNotEqual(v["GF_SECURITY_ADMIN_PASSWORD"], "admin")

    def test_existing_strong_admin_values_are_kept(self):
        self.write_env(INFLUXDB_ADMIN_PASSWORD="strongpw", INFLUXDB_ADMIN_TOKEN="strongtoken")
        self.exists("renewvan-influxdb", "renewvan-grafana")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        v = self.values()
        self.assertEqual(v["INFLUXDB_ADMIN_PASSWORD"], "strongpw")
        self.assertEqual(v["INFLUXDB_ADMIN_TOKEN"], "strongtoken")
        self.assertNotIn("auth delete", self.calls())
        self.assertNotIn("user password", self.calls())
        self.assertIn("reset-admin-password", self.calls(), "Grafana volume predates the variable")
        self.assertNotIn("INFLUXDB_ADMIN", r.stdout)

    def test_second_run_changes_nothing(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.envfile.read_text()
        (self.state / "calls.log").unlink()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.envfile.read_text(), before)
        self.assertIn("nothing to change", r.stdout)
        self.assertNotIn("auth create", self.calls())

    def test_missing_env_file_fails(self):
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
