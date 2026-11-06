"""bin/validate-broker-config.sh against a fake `docker` standing in for the throwaway broker."""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "validate-broker-config.sh"

# Exit code of the fake throwaway broker. A broker that stayed up until `timeout` stopped it exits 0
# on eclipse-mosquitto 2.1.2 (it handles SIGTERM; checked against the real image). A startup
# failure exits 1 or 3.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >>"$FAKE_DOCKER_LOG"
[[ "$1" == run ]] && exit "${FAKE_BROKER_RC:-124}"
exit 0
"""


class ValidateBrokerConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.etc = self.dir / "etc"
        self.mosq = self.etc / "mosquitto"
        self.mosq.mkdir(parents=True)
        (self.dir / "docker" / "mosquitto" / "config").mkdir(parents=True)
        for rel in ("config/mosquitto.conf", "auth.conf"):
            shutil.copy(
                ROOT / "docker" / "mosquitto" / rel, self.dir / "docker" / "mosquitto" / rel
            )
        (self.dir / "docker-compose.yml").write_text(
            "services:\n  mosquitto:\n    image: eclipse-mosquitto:test\n    restart: always\n"
        )
        (self.mosq / "passwd").write_text("tank:$7$new\n")
        (self.mosq / "acl").write_text("topic read renewvan/#\n")
        (self.mosq / "mosquitto.conf").write_text("listener 1883\n")
        bindir = self.dir / "fakebin"
        bindir.mkdir()
        docker = bindir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        self.log = self.dir / "docker.log"
        self.env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "RENEWVAN_ETC": str(self.etc),
            "FAKE_DOCKER_LOG": str(self.log),
            "VALIDATE_BROKER_SECONDS": "1",
        }

    def run_script(self, **extra):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            cwd=self.dir,
        )

    def test_valid_files_install_the_conf_with_auth_on_both_listeners(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        conf = (self.mosq / "mosquitto.conf").read_text()
        self.assertIn("listener 1883", conf)
        self.assertIn("listener 9001", conf)
        self.assertIn("allow_anonymous true", conf)
        self.assertIn("password_file /mosquitto/config/passwd", conf)
        self.assertIn("acl_file /mosquitto/config/acl", conf)
        active = [ln for ln in conf.splitlines() if not ln.lstrip().startswith("#")]
        self.assertNotIn("per_listener_settings true", active, "global auth covers both listeners")
        self.assertFalse((self.mosq / "mosquitto.conf.new").exists())
        self.assertTrue((self.mosq / "mosquitto.conf.changed").exists())
        run = self.log.read_text()
        self.assertIn("--network none", run)
        self.assertIn(f"{self.mosq}:/mosquitto/config:ro", run, "mounted read-only")
        self.assertIn("mosquitto.conf.new", run, "the candidate is what gets tested")

    def test_a_broker_that_stayed_up_exits_zero_or_with_timeout_codes(self):
        for rc in ("0", "124", "143"):
            with self.subTest(rc=rc):
                self.assertEqual(self.run_script(FAKE_BROKER_RC=rc).returncode, 0)
        for rc in ("1", "3"):
            with self.subTest(rc=rc):
                self.assertEqual(self.run_script(FAKE_BROKER_RC=rc).returncode, 1)

    def test_unchanged_conf_leaves_no_restart_marker(self):
        self.assertEqual(self.run_script().returncode, 0)
        (self.mosq / "mosquitto.conf.changed").unlink()
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse((self.mosq / "mosquitto.conf.changed").exists())

    def test_bad_acl_stops_restores_the_previous_files_and_keeps_the_conf(self):
        (self.mosq / "acl.prev").write_text("topic read renewvan/#\n# good\n")
        (self.mosq / "acl").write_text("topic wirte renewvan/#\n")  # the broker exits on this
        r = self.run_script(FAKE_BROKER_RC="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("running broker untouched", r.stderr)
        self.assertEqual((self.mosq / "mosquitto.conf").read_text(), "listener 1883\n")
        self.assertFalse((self.mosq / "mosquitto.conf.new").exists())
        self.assertIn("# good", (self.mosq / "acl").read_text(), "previous ACL restored")
        self.assertFalse((self.mosq / "mosquitto.conf.changed").exists())

    def test_missing_acl_refuses(self):
        (self.mosq / "acl").unlink()
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.log.exists(), "no container is started without the files")

    def test_deploy_validates_before_compose_and_reloads_after(self):
        deploy = (ROOT / "bin" / "deploy.sh").read_text()
        validate = deploy.index("bash bin/validate-broker-config.sh")
        self.assertGreater(validate, deploy.index("bash bin/ensure-secrets.sh"))
        self.assertLess(validate, deploy.index("\ndocker compose pull\n"))
        self.assertGreater(deploy.index("kill -s HUP renewvan-mosquitto"), validate)
        self.assertIn(
            "bin/validate-broker-config.sh", deploy.split("rsync -az bin/vedirect-gate.sh")[1][:200]
        )


if __name__ == "__main__":
    unittest.main()
