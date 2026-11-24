"""The broker client registry, bin/broker-clients.conf: bin/broker-accounts.sh reads it, and every
copy of its knowledge elsewhere (compose, .env.example, the host plugins) is checked against it."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import broker_clients

BIN = Path(__file__).resolve().parent
ACCOUNTS = BIN / "broker-accounts.sh"


def shell(snippet, boards="", registry=None):
    """Run `snippet` with bin/broker-accounts.sh sourced against a throwaway .env."""
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(f"BROKER_ESP32_BOARDS={boards}\n")
        pre = f"BROKER_CLIENTS_FILE={registry}; " if registry else ""
        return subprocess.run(
            ["bash", "-c", f"{pre}ENV_FILE={env}; source {ACCOUNTS}\n{snippet}"],
            capture_output=True,
            text=True,
            cwd=tmp,
        )


class BrokerAccountsReadTheRegistryTest(unittest.TestCase):
    def test_accounts_follow_a_registry_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "clients.conf"
            registry.write_text(
                "# comment\n\nclient alpha compose svc-a\nclient beta envfile b.env b-unit\n"
                "write beta renewvan/beta/#\nwrite esp32-<name> renewvan/esp32-<name>/x\n"
                "write beta renewvan/other/+\n"
            )
            r = shell(
                "client_keys; client_spec beta; echo; client_writes beta; client_writes esp32-f",
                boards="f",
                registry=registry,
            )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.splitlines(),
            [
                "alpha",
                "beta",
                "esp32-f",
                "beta:envfile:b.env:b-unit",
                "renewvan/beta/#",
                "renewvan/other/+",
                "renewvan/esp32-f/x",
            ],
        )

    def test_a_missing_or_malformed_registry_fails_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.conf"
            bad.write_text("client alpha compose svc\nallow alpha renewvan/#\n")
            for registry in (Path(tmp) / "absent.conf", bad):
                r = shell('echo "source rc=$?"', registry=registry)
                self.assertEqual(r.stdout, "source rc=1\n", registry)
                self.assertIn(str(registry), r.stderr, registry)

    def test_the_shell_reads_the_registry_the_tests_read(self):
        clients, board = broker_clients.load()
        script = "for k in $(client_keys); do client_spec $k; echo; client_writes $k; echo .; done"
        r = shell(script, boards="front")
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = []
        for c in clients:
            spec = ":".join([c.key, c.kind, c.target] + ([c.unit] if c.unit else []))
            expected += [spec, *c.writes, "."]
        expected += ["esp32-front:esp32:", *broker_clients.board_writes("front", board), "."]
        self.assertEqual(r.stdout.splitlines(), expected)

    def test_deploy_ships_the_registry_next_to_the_script_that_reads_it(self):
        # the Pi's ensure-secrets and the deploy's restart loop source broker-accounts.sh there
        deploy = (BIN / "deploy.sh").read_text()
        self.assertRegex(
            deploy, r"rsync -az [^\n]*bin/broker-accounts\.sh bin/broker-clients\.conf [^\n]*/bin/"
        )


if __name__ == "__main__":
    unittest.main()
