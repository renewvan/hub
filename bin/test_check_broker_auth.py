"""bin/check-broker-auth.sh and bin/cutover-broker-acl.sh against fakes for docker and systemctl."""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent

# `docker exec -e PROBE_USER ...` is a probe: the publish is "delivered" only for the (user|topic)
# pairs in FAKE_ALLOWED. A plain `docker exec ... mosquitto_sub` (the demo state read) says `false`.
# A plain `mosquitto_pub` is the anonymous-connection test: refused (rc 5) when FAKE_ANON_REFUSED=1.
FAKE_DOCKER = r"""#!/usr/bin/env bash
case "$1" in
  logs) cat "$FAKE_LOGS" ;;
  compose) echo "$FAKE_SERVICES" | tr ' ' '\n' ;;
  exec)
    if [[ "$*" == *PROBE_USER* ]]; then
      case " $FAKE_ALLOWED " in *" $PROBE_USER|$PROBE_TOPIC "*) echo "$PROBE_MSG" ;; esac
    elif [[ "$*" == *mosquitto_pub* ]]; then
      [[ "${FAKE_ANON_REFUSED:-1}" == 1 ]] && exit 5
    else
      echo false
    fi ;;
esac
exit 0
"""

SET_PROBE = "renewvan/relay/zz-check-probe/set"
DEMO_TOGGLE = "renewvan/demo/enabled/set"


def connect(client_id, user=None):
    u = f", u'{user}'" if user else ""
    return (
        f"1700000000: New client connected from 172.18.0.5:4000 as {client_id} (p2, c1, k60{u}).\n"
    )


def make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class CheckBrokerAuthTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        fakebin = self.dir / "fakebin"
        fakebin.mkdir()
        for name, body in (
            ("docker", FAKE_DOCKER),
            ("systemctl", "#!/bin/sh\nexit 3\n"),
        ):  # exit 3: no host plugin is running
            (fakebin / name).write_text(body)
            make_executable(fakebin / name)
        self.envfile = self.dir / ".env"
        self.envfile.write_text("BROKER_ACL_OPEN=0\nMQTT_READER_PASSWORD=readerpw\n")
        self.logs = self.dir / "logs"
        self.allowed = [f"command|{SET_PROBE}", f"command|{DEMO_TOGGLE}"]
        self.services = "driver-tank logger"
        self.logs.write_text(connect("tank-1", "tank") + connect("lg", "logger"))
        self.env = {
            **os.environ,
            "PATH": f"{fakebin}:{os.environ['PATH']}",
            "ENV_FILE": str(self.envfile),
            "FAKE_LOGS": str(self.logs),
        }

    def run_check(self, *args, **extra):
        env = {
            **self.env,
            "FAKE_ALLOWED": " ".join(self.allowed),
            "FAKE_SERVICES": self.services,
            **extra,
        }
        return subprocess.run(
            ["bash", str(BIN / "check-broker-auth.sh"), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.dir,
        )

    def test_passes_when_every_client_has_its_own_account_and_writes_are_scoped(self):
        r = self.run_check("--stage3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok    tank is connected as tank", r.stdout)
        self.assertIn(f"ok    command can write {SET_PROBE}", r.stdout)
        self.assertIn(f"ok    demo cannot write {DEMO_TOGGLE}", r.stdout)
        self.assertIn("ok    anonymous cannot connect", r.stdout)
        self.assertIn(f"ok    reader cannot write {SET_PROBE}", r.stdout)

    def test_a_running_client_without_its_own_connection_fails(self):
        self.logs.write_text(connect("tank-1", "tank") + connect("lg"))  # logger still anonymous
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("logger runs but no connection as 'logger'", r.stderr)

    def test_the_demo_driver_writing_the_toggle_fails(self):
        self.allowed.append(f"demo|{DEMO_TOGGLE}")
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"demo CAN write {DEMO_TOGGLE}", r.stderr)

    def test_command_unable_to_write_a_set_topic_fails(self):
        self.allowed = []
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"command cannot write {SET_PROBE} (it must)", r.stderr)

    def test_a_driver_or_the_esp32_writing_a_set_topic_fails(self):
        self.allowed.append(f"esp32|{SET_PROBE}")
        r = self.run_check()
        self.assertIn("esp32 CAN write", r.stderr)
        self.assertEqual(r.returncode, 1)

    def test_stage3_fails_on_an_anonymous_client_unless_allowed(self):
        self.logs.write_text(self.logs.read_text() + connect("auto-9"))
        r = self.run_check("--stage3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("anonymous clients still connected: auto-9", r.stderr)
        ok = self.run_check("--stage3", CHECK_ALLOW_ANON="^auto-")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_a_disconnected_anonymous_client_does_not_count(self):
        gone = "1700000100: Client auto-9 closed its connection.\n"
        self.logs.write_text(self.logs.read_text() + connect("auto-9") + gone)
        self.assertEqual(self.run_check("--stage3").returncode, 0)

    def test_mosquitto_2_1_disconnect_lines_with_an_address_count(self):
        # Real eclipse-mosquitto 2.1.2 wording: "Client <id> [<addr>] disconnected: <reason>."
        gone = "1700000100: Client auto-9 [172.18.0.5:4000] disconnected: Broken pipe.\n"
        self.logs.write_text(self.logs.read_text() + connect("auto-9") + gone)
        self.assertEqual(self.run_check("--stage3").returncode, 0)

    def test_the_reader_writing_a_set_topic_fails(self):
        self.allowed.append(f"reader|{SET_PROBE}")
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("reader CAN write", r.stderr)

    def test_anonymous_refusal_is_only_checked_after_the_cutover(self):
        self.envfile.write_text("BROKER_ACL_OPEN=1\n")
        r = self.run_check(FAKE_ANON_REFUSED="0")  # still migrating: anonymous may connect
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("BROKER_ACL_OPEN=1", r.stdout)
        self.envfile.write_text("BROKER_ACL_OPEN=0\n")
        r = self.run_check(FAKE_ANON_REFUSED="0")
        self.assertEqual(
            r.returncode, 1, "anonymous still connecting after the cutover is a failure"
        )
        self.assertIn("anonymous client can still connect", r.stderr)


class CutoverTest(unittest.TestCase):
    """The cutover with stub scripts: order, and the rollback on a failed check."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for name in ("cutover-broker-acl.sh", "broker-accounts.sh"):
            shutil.copy(BIN / name, self.bin / name)
        self.log = self.dir / "log"
        self.log.write_text("")
        stub = (
            '#!/usr/bin/env bash\necho "$(basename "$0") $* acl=$(sed -n "s/^BROKER_ACL_OPEN=//p" '
            '"$ENV_FILE")" >>"$LOG"\n%s\n'
        )
        (self.bin / "ensure-secrets.sh").write_text(stub % "exit 0")
        (self.bin / "validate-broker-config.sh").write_text(stub % "exit 0")
        # check-broker-auth fails in the mode named by FAIL_CHECK: stage3 or final (no flag)
        (self.bin / "check-broker-auth.sh").write_text(
            stub % '[[ "${FAIL_CHECK:-}" == stage3 && "${1:-}" == --stage3 ]] && exit 1\n'
            '[[ "${FAIL_CHECK:-}" == final && -z "${1:-}" ]] && exit 1\nexit 0'
        )
        fakebin = self.dir / "fakebin"
        fakebin.mkdir()
        (fakebin / "docker").write_text('#!/bin/sh\necho "docker $*" >>"$LOG"\n')
        make_executable(fakebin / "docker")
        for f in self.bin.glob("*.sh"):
            make_executable(f)
        self.envfile = self.dir / ".env"
        self.envfile.write_text("BROKER_ACL_OPEN=1\n")
        self.env = {
            **os.environ,
            "PATH": f"{fakebin}:{os.environ['PATH']}",
            "ENV_FILE": str(self.envfile),
            "LOG": str(self.log),
        }

    def run_cutover(self, *args, **extra):
        return subprocess.run(
            ["bash", str(self.bin / "cutover-broker-acl.sh"), *args],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            cwd=self.dir,
        )

    def acl_open(self):
        return self.envfile.read_text().strip().split("=")[1]

    def test_cutover_checks_stage3_then_closes_validates_reloads_and_rechecks(self):
        r = self.run_cutover()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.acl_open(), "0")
        steps = self.log.read_text().splitlines()
        self.assertEqual(steps[0], "check-broker-auth.sh --stage3 acl=1")
        order = [s.split(" ")[0] for s in steps[1:]]
        self.assertEqual(
            order,
            ["ensure-secrets.sh", "validate-broker-config.sh", "docker", "check-broker-auth.sh"],
        )
        self.assertIn("docker kill -s HUP renewvan-mosquitto", steps[3])

    def test_a_failed_stage3_leaves_the_acl_alone(self):
        r = self.run_cutover(FAIL_CHECK="stage3")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.acl_open(), "1")
        self.assertNotIn("ensure-secrets.sh", self.log.read_text())

    def test_a_failed_final_check_restores_the_migration_line(self):
        r = self.run_cutover(FAIL_CHECK="final")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.acl_open(), "1")
        self.assertIn("restoring the migration line", r.stderr)
        self.assertIn("HUP", self.log.read_text().splitlines()[-1])

    def test_rollback_restores_the_line_and_reloads(self):
        self.envfile.write_text("BROKER_ACL_OPEN=0\n")
        r = self.run_cutover("--rollback")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.acl_open(), "1")
        self.assertIn("HUP", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
