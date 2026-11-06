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
  run)
    case "$*" in
      *mosq:test*) while read -r u p; do echo "$u:\$7\$fake\$$(printf %s "$p" | rev)"; done ;;
      *caddy:test*) echo "\$2a\$14\$fakehash$(printf %s "$CADDY_PW" | rev)" ;;
    esac ;;
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


class Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        self.etc = self.dir / "etc"
        self.etc.mkdir()
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
            "RENEWVAN_ETC": str(self.etc),
            "MOSQUITTO_IMAGE": "mosq:test",
            "CADDY_IMAGE": "caddy:test",
            "ENSURE_SECRETS_CHOWN": "true",
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


class EnsureSecretsTest(Harness):
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


CLIENTS = (
    "tank gps temperature tilt wattcycle autoterm vedirect logger venus router demo "
    "kiosk tailscale hub esp32 command reader"
).split()


def client_var(key):
    return f"MQTT_{key.upper()}_PASSWORD"


class BrokerAccountsTest(Harness):
    """ensure-secrets.sh and rotate-secret.sh for the broker accounts (ADR 0025)."""

    def setUp(self):
        super().setUp()
        for name in ("tank", "gps", "temperature", "tilt", "wattcycle"):
            (self.etc / name).mkdir()
            (self.etc / name / "config.ini").write_text(
                "# my calibration\n[mqtt]\nhost = mosquitto\n\n[driver]\nsensor_min = 3\n"
            )
        (self.etc / "kiosk.env").write_text("DISPLAY_ON_CMD=x\nMQTT_USERNAME=\n")
        (self.dir / "docker-compose.yml").write_text("services:\n")

    def acl(self):
        return (self.etc / "mosquitto" / "acl").read_text()

    def acl_block(self, user):
        text = self.acl()
        start = text.index(f"\nuser {user}\n")
        end = text.find("\nuser ", start + 1)
        return text[start : end if end != -1 else None]

    def run_rotate(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT.with_name("rotate-secret.sh")), *args],
            capture_output=True,
            text=True,
            env={**self.env, "ENV_FILE": str(self.envfile)},
            cwd=self.dir,
        )

    def test_generates_one_distinct_password_per_client(self):
        self.write_env()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        v = self.values()
        pws = [v[client_var(k)] for k in CLIENTS]
        self.assertTrue(all(len(p) == 32 for p in pws), pws)
        self.assertEqual(len(set(pws)), len(CLIENTS))
        for p in pws:
            self.assertNotIn(p, r.stdout + r.stderr, "client passwords are never printed")

    def test_password_file_has_every_client_hashed(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        v = self.values()
        lines = (self.etc / "mosquitto" / "passwd").read_text().splitlines()
        self.assertEqual(sorted(line.split(":")[0] for line in lines), sorted(CLIENTS))
        text = "\n".join(lines)
        for k in CLIENTS:
            self.assertNotIn(v[client_var(k)], text, "the password file holds hashes only")
        self.assertEqual(stat.S_IMODE((self.etc / "mosquitto" / "passwd").stat().st_mode), 0o640)

    def test_caddy_login_is_hashed_and_shown_once(self):
        self.write_env()
        r = self.run_script()
        v = self.values()
        self.assertEqual(v["CADDY_BASIC_AUTH_USER"], "renewvan")
        self.assertTrue(v["CADDY_BASIC_AUTH_HASH"].startswith("'$2a$"), "single-quoted for compose")
        pw = r.stdout.split("password ")[1].split()[0]
        self.assertNotIn(pw, self.envfile.read_text(), "only the hash is stored")
        again = self.run_script()
        self.assertNotIn("password ", again.stdout, "shown once")

    def test_acl_content(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        acl = self.acl()
        head = acl.split("\nuser ")[0]
        # `pattern`, not `topic`: a global `topic` line applies to anonymous clients only, so
        # authenticated drivers would read nothing (checked on eclipse-mosquitto 2.1.2)
        self.assertIn("pattern read renewvan/#", head)
        self.assertNotIn("\ntopic read", "\n" + head)
        self.assertIn("pattern readwrite renewvan/#", head, "temporary migration line on a new van")
        self.assertEqual(self.values()["BROKER_ACL_OPEN"], "1")
        # drivers write only their own namespaces, never a set topic of another client
        self.assertIn("topic write renewvan/tank/#", self.acl_block("tank"))
        self.assertNotIn("relay", self.acl_block("tank"))
        self.assertIn("topic write renewvan/battery/#", self.acl_block("wattcycle"))
        self.assertIn("topic write renewvan/heater/#", self.acl_block("autoterm"))
        # command writes only set topics
        command = self.acl_block("command")
        for topic in (
            "renewvan/relay/+/set",
            "renewvan/relay/+/name/set",
            "renewvan/kiosk/display/+/set",
            "renewvan/heater/+/set",
            "renewvan/demo/enabled/set",
        ):
            self.assertIn(f"topic write {topic}\n", command + "\n")
        for line in command.splitlines():
            if line.startswith("topic write"):
                self.assertTrue(line.endswith("/set"), line)
        # demo may not write the toggle, the ESP32 may not write set topics
        demo = self.acl_block("demo")
        self.assertNotIn("demo/enabled/set", demo)
        self.assertNotIn("renewvan/demo/#", demo)
        for line in self.acl_block("esp32").splitlines():
            self.assertFalse(line.endswith("/set"), line)
        # the read-only login has no write line at all
        self.assertNotIn("topic", self.acl_block("reader"))
        for k in CLIENTS:
            self.assertIn(f"\nuser {k}\n", acl)

    def test_acl_without_the_migration_line_after_cutover(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        text = self.envfile.read_text().replace("BROKER_ACL_OPEN=1", "BROKER_ACL_OPEN=0")
        self.envfile.write_text(text)
        self.assertEqual(self.run_script().returncode, 0)
        self.assertNotIn("readwrite", self.acl(), "the migration line is gone")
        self.assertIn("pattern read renewvan/#", self.acl(), "everyone still reads")
        self.assertEqual(self.values()["BROKER_ACL_OPEN"], "0", "a redeploy does not reopen it")

    def test_credentials_land_in_each_clients_own_file_and_keep_the_rest(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        v = self.values()
        ini = (self.etc / "tank" / "config.ini").read_text()
        self.assertIn("# my calibration", ini)
        self.assertIn("sensor_min = 3", ini)
        self.assertIn("username = tank", ini)
        self.assertIn(f"password = {v[client_var('tank')]}", ini)
        kiosk = (self.etc / "kiosk.env").read_text()
        self.assertIn("DISPLAY_ON_CMD=x", kiosk)
        self.assertIn("MQTT_USERNAME=kiosk", kiosk)
        self.assertEqual(kiosk.count("MQTT_USERNAME"), 1)
        self.assertIn(f"MQTT_PASSWORD={v[client_var('kiosk')]}", kiosk)
        changed = (self.dir / ".env.changed-clients").read_text().split()
        self.assertEqual(
            sorted(changed), ["gps", "kiosk", "tank", "temperature", "tilt", "wattcycle"]
        )

    def snapshot(self):
        files = [self.envfile, *self.etc.rglob("*")]
        return {str(f): f.read_text() for f in files if f.is_file()}

    def test_second_run_changes_nothing(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.snapshot()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)
        # pending restarts are kept until deploy clears them, but nothing new is added
        pending = (self.dir / ".env.changed-clients").read_text().split()
        self.assertEqual(len(pending), len(set(pending)))
        self.assertIn("nothing to change", r.stdout)

    def test_rotation_changes_exactly_one_client(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.values()
        passwd_before = (self.etc / "mosquitto" / "passwd").read_text()
        r = self.run_rotate("gps")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.values()
        changed = [k for k in before if before[k] != after.get(k)]
        self.assertEqual(changed, [client_var("gps")])
        self.assertIn(
            f"password = {after[client_var('gps')]}", (self.etc / "gps" / "config.ini").read_text()
        )
        lines = dict(
            ln.split(":", 1) for ln in (self.etc / "mosquitto" / "passwd").read_text().splitlines()
        )
        old = dict(ln.split(":", 1) for ln in passwd_before.splitlines())
        self.assertNotEqual(lines["gps"], old["gps"])
        self.assertIn(after[client_var("gps")][::-1], lines["gps"])
        self.assertIn("kill -s HUP renewvan-mosquitto", self.calls())
        self.assertIn(
            "compose restart driver-gps", self.calls()
        )  # config.ini is re-read on restart
        self.assertNotIn(after[client_var("gps")], r.stdout + r.stderr)

    def test_rotation_refuses_unknown_clients_and_esp32(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.values()
        for bad in ("nope", "esp32", ""):
            r = self.run_rotate(bad) if bad else self.run_rotate()
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertEqual(self.values(), before)

    def test_rotating_the_caddy_login_shows_the_new_password_once(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.values()["CADDY_BASIC_AUTH_HASH"]
        r = self.run_rotate("caddy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(self.values()["CADDY_BASIC_AUTH_HASH"], before)
        self.assertIn("password ", r.stdout)
        self.assertIn("compose up -d --force-recreate --no-deps caddy", self.calls())


if __name__ == "__main__":
    unittest.main()
