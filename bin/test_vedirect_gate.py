"""Tests for bin/vedirect-gate.sh, the deploy-time check behind the `vedirect` profile."""

import subprocess
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).with_name("vedirect-gate.sh")
DUPPA = "usb-Duppa_srl_USB_QUADPORT_VE.DIRECT_X-if02-port0"


def run(by_id: Path, config: Path | None = None) -> int:
    args = ["bash", str(GATE), str(by_id)]
    if config is not None:
        args.append(str(config))
    return subprocess.run(args, capture_output=True).returncode


class GateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.by_id = self.dir / "by-id"
        self.by_id.mkdir()

    def link(self, name):
        (self.by_id / name).symlink_to("../../ttyUSB0")

    def test_missing_directory_is_off(self):
        self.assertNotEqual(run(self.dir / "absent"), 0)

    def test_empty_directory_is_off(self):
        self.assertNotEqual(run(self.by_id), 0)

    def test_default_globs_match(self):
        for name in (
            DUPPA,
            "usb-VictronEnergy_VE.Direct_HQ1-if00-port0",
            "usb-FTDI_FT232R-if00-port0",
        ):
            with self.subTest(name=name):
                self.link(name)
                self.assertEqual(run(self.by_id), 0)
                (self.by_id / name).unlink()

    def test_unrelated_adapter_is_off(self):
        self.link("usb-Silicon_Labs_CP2102-if00-port0")
        self.assertNotEqual(run(self.by_id), 0)

    def test_config_port_globs_override(self):
        self.link("usb-Silicon_Labs_CP2102-if00-port0")
        cfg = self.dir / "config.ini"
        cfg.write_text("[serial]\nport_globs = usb-Foo_*, usb-Silicon_Labs_*\n")
        self.assertEqual(run(self.by_id, cfg), 0)

    def test_config_override_replaces_defaults(self):
        self.link(DUPPA)
        cfg = self.dir / "config.ini"
        cfg.write_text("[serial]\nport_globs = usb-Other_*\n")
        self.assertNotEqual(run(self.by_id, cfg), 0)

    def test_commented_or_absent_override_uses_defaults(self):
        self.link(DUPPA)
        cfg = self.dir / "config.ini"
        cfg.write_text("[mqtt]\nhost = mosquitto\n; port_globs = usb-Other_*\n")
        self.assertEqual(run(self.by_id, cfg), 0)
        self.assertEqual(run(self.by_id, self.dir / "missing.ini"), 0)


if __name__ == "__main__":
    unittest.main()
