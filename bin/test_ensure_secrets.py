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
    "kiosk tailscale hub esp32-front esp32-rear command reader"
).split()


def client_var(key):
    return f"MQTT_{key.upper().replace('-', '_')}_PASSWORD"


BROKER_ACCOUNTS = SCRIPT.with_name("broker-accounts.sh")


class BoardListTest(unittest.TestCase):
    """BROKER_ESP32_BOARDS as every consumer of bin/broker-accounts.sh sees it (hub#162)."""

    def shell(self, boards_line, snippet, pre=""):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(boards_line + "\n")
            return subprocess.run(
                ["bash", "-c", f"ENV_FILE={env}; source {BROKER_ACCOUNTS}; {pre}\n{snippet}"],
                capture_output=True,
                text=True,
                cwd=tmp,  # a glob would expand against this directory
            )

    def boards(self, boards_line, **kw):
        r = self.shell(boards_line, "board_names", **kw)
        return r.stdout.split(), r.stderr

    def test_quotes_around_the_value_are_stripped_like_compose_env_file(self):
        self.assertEqual(self.boards('BROKER_ESP32_BOARDS="front rear"')[0], ["front", "rear"])
        self.assertEqual(self.boards("BROKER_ESP32_BOARDS='front'")[0], ["front"])

    def test_a_glob_is_never_expanded(self):
        names, err = self.boards("BROKER_ESP32_BOARDS=*")
        self.assertEqual(names, [])
        self.assertIn("BROKER_ESP32_BOARDS", err)

    def test_an_invalid_list_yields_no_boards_at_all_and_says_why(self):
        for bad in ("Front", "front front", "fr.nt", "front-", "front rear/#", "9front", '""x'):
            names, err = self.boards(f"BROKER_ESP32_BOARDS={bad}")
            self.assertEqual(names, [], bad)
            self.assertIn("BROKER_ESP32_BOARDS", err, bad)

    def test_empty_or_quote_only_lists_are_no_boards_and_no_error(self):
        for empty in ("BROKER_ESP32_BOARDS=", 'BROKER_ESP32_BOARDS=""', "# nothing"):
            names, err = self.boards(empty)
            self.assertEqual((names, err), ([], ""), empty)

    def test_client_keys_and_client_spec_carry_only_checked_boards(self):
        r = self.shell("BROKER_ESP32_BOARDS=front", "client_keys")
        self.assertIn("esp32-front", r.stdout.split())
        r = self.shell("BROKER_ESP32_BOARDS=front Front", "client_keys")
        self.assertFalse([k for k in r.stdout.split() if k.startswith("esp32-")])

    def test_client_spec_matches_board_names_as_fixed_strings(self):
        spec = "client_spec esp32-front"
        self.assertEqual(self.shell("BROKER_ESP32_BOARDS=front", spec).stdout, "esp32-front:esp32:")
        for line in (
            "BROKER_ESP32_BOARDS=",
            "BROKER_ESP32_BOARDS=rear",
            "BROKER_ESP32_BOARDS=f.ont",
        ):
            self.assertNotEqual(self.shell(line, spec).returncode, 0, line)
        # an empty list must not make the bare prefix a client
        self.assertNotEqual(self.shell("BROKER_ESP32_BOARDS=", "client_spec esp32-").returncode, 0)

    def test_boards_do_not_depend_on_any_particular_static_client(self):
        r = self.shell(
            "BROKER_ESP32_BOARDS=front", "client_keys", pre='BROKER_CLIENTS=("tank:config.ini:t")'
        )
        self.assertEqual(r.stdout.split(), ["tank", "esp32-front"])


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

    def write_env(self, **values):
        super().write_env(**values)
        with self.envfile.open("a") as f:
            f.write("BROKER_ESP32_BOARDS=front rear\n")  # ADR 0027: one account per board

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

    def test_dashboard_login_is_hashed_and_shown_once(self):
        self.write_env()
        r = self.run_script()
        v = self.values()
        self.assertEqual(v["LOGIN_USER"], "renewvan")
        self.assertTrue(
            v["LOGIN_PASSWORD_HASH"].startswith("scrypt:"), "scrypt, no $ for compose to expand"
        )
        self.assertGreaterEqual(len(v["LOGIN_SESSION_SECRET"]), 32)
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
        self.assertNotIn("\ntopic read renewvan/#\n", "\n" + head + "\n")
        # `topic` (anonymous only), never `pattern`: accounts are scoped during the migration too
        self.assertIn("topic readwrite renewvan/#", head, "temporary migration line on a new van")
        self.assertNotIn("pattern readwrite", head)
        self.assertEqual(self.values()["BROKER_ACL_OPEN"], "1")
        # drivers write only their own namespaces, never a set topic of another client
        self.assertIn("topic write renewvan/tank/#", self.acl_block("tank"))
        self.assertNotIn("relay", self.acl_block("tank"))
        self.assertIn("topic write renewvan/battery/#", self.acl_block("wattcycle"))
        autoterm = self.acl_block("autoterm")
        # info, health, last will: renewvan/autoterm/ (pinned v0.2.3); telemetry: renewvan/heater/
        for topic in ("renewvan/autoterm/info", "renewvan/autoterm/health", "renewvan/heater/+/+"):
            self.assertIn(f"topic write {topic}\n", autoterm + "\n")
        # it only subscribes to its set topics: no write on them, no wildcard over either namespace
        self.assertNotIn("renewvan/heater/#", autoterm)
        self.assertNotIn("renewvan/autoterm/#", autoterm)
        for line in autoterm.splitlines():
            self.assertFalse(line.endswith("/set"), line)
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
        for board in ("esp32-front", "esp32-rear"):
            for line in self.acl_block(board).splitlines():
                self.assertFalse(line.endswith("/set"), line)
        # the read-only login has no write line at all
        self.assertNotIn("topic", self.acl_block("reader"))
        for k in CLIENTS:
            self.assertIn(f"\nuser {k}\n", acl)

    def test_each_board_writes_only_its_own_driver_namespace(self):
        # hub#162, ADR 0027: a board's key is esp32-<name>; it may not write another board's topics
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        for mine, other in (("esp32-front", "esp32-rear"), ("esp32-rear", "esp32-front")):
            block = self.acl_block(mine)
            self.assertIn(f"topic write renewvan/{mine}/#\n", block + "\n")
            self.assertNotIn(other, block)
            self.assertNotIn("renewvan/esp32/#", block, "no family-wide grant")
            self.assertNotIn("esp32-+", block, "no wildcard over board keys")
            self.assertNotIn("renewvan/relay/#", block, "relay topics are listed, never wholesale")
            self.assertIn("topic write renewvan/relay/+/state\n", block + "\n")
        self.assertNotIn("\nuser esp32\n", self.acl(), "the old family account is gone")

    def test_no_boards_means_no_board_accounts(self):
        super().write_env()
        self.assertEqual(self.run_script().returncode, 0)
        self.assertNotIn("user esp32", self.acl())
        self.assertNotIn(client_var("esp32"), self.values())

    def test_a_board_added_later_gets_an_account_and_the_others_keep_theirs(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.values()
        self.envfile.write_text(self.envfile.read_text().replace("front rear", "front rear garage"))
        self.assertEqual(self.run_script().returncode, 0)
        after = self.values()
        self.assertEqual(len(after[client_var("esp32-garage")]), 32)
        self.assertEqual(after[client_var("esp32-front")], before[client_var("esp32-front")])
        users = [
            ln.split(":")[0] for ln in (self.etc / "mosquitto" / "passwd").read_text().splitlines()
        ]
        self.assertIn("esp32-garage", users)
        self.assertIn(
            "topic write renewvan/esp32-garage/#\n", self.acl_block("esp32-garage") + "\n"
        )

    def test_dropping_a_board_warns_loudly_but_does_not_block(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        self.envfile.write_text(self.envfile.read_text().replace("front rear", "front"))
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertIn("esp32-rear", r.stderr)
        self.assertIn("MQTT_ESP32_REAR_PASSWORD", r.stderr)
        self.assertNotIn("esp32-front", r.stderr, "a board that stays is not reported")
        users = [
            ln.split(":")[0] for ln in (self.etc / "mosquitto" / "passwd").read_text().splitlines()
        ]
        self.assertNotIn("esp32-rear", users, "the run still drops it")
        again = self.run_script()
        self.assertNotIn("WARNING", again.stderr, "only when something is about to be dropped")

    def test_the_legacy_esp32_account_is_reported_when_it_is_dropped(self):
        self.write_env()
        mosq = self.etc / "mosquitto"
        mosq.mkdir()
        (mosq / "passwd").write_text("esp32:$7$old$hash\ntank:$7$old$hash\n")
        (mosq / "acl").write_text(
            "pattern read renewvan/#\n\nuser esp32\ntopic write renewvan/esp32/#\n"
        )
        self.envfile.write_text(self.envfile.read_text() + "MQTT_ESP32_PASSWORD=legacy\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertRegex(r.stderr, r"\besp32\b")
        self.assertIn("MQTT_ESP32_PASSWORD", r.stderr)
        self.assertNotIn("legacy", r.stderr, "never print the value")

    def test_a_malformed_board_name_is_refused(self):
        self.write_env()
        self.envfile.write_text(self.envfile.read_text().replace("front rear", "Front rear/#"))
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BROKER_ESP32_BOARDS", r.stderr)

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
        for bad in ("nope", "esp32", "esp32-front", ""):
            r = self.run_rotate(bad) if bad else self.run_rotate()
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertEqual(self.values(), before)

    def test_rotating_the_login_shows_the_new_password_once(self):
        self.write_env()
        self.assertEqual(self.run_script().returncode, 0)
        before = self.values()["LOGIN_PASSWORD_HASH"]
        r = self.run_rotate("login")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(self.values()["LOGIN_PASSWORD_HASH"], before)
        self.assertIn("password ", r.stdout)
        self.assertIn("compose up -d --force-recreate --no-deps login", self.calls())


if __name__ == "__main__":
    unittest.main()
