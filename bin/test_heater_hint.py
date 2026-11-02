"""
Tests for bin/heater-hint.sh, the message bin/deploy.sh prints when /dev/ttyAUTOTERM is missing.

Run: python3 -m unittest bin.test_heater_hint -v
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "heater-hint.sh"
RULE_NAME = "99-renewvan-autoterm.rules"


class HeaterHintTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.usb = self.root / "usb"
        self.rules = self.root / "rules"
        self.usb.mkdir()
        self.rules.mkdir()

    def add_usb(self, name, vid, pid, serial):
        d = self.usb / name
        d.mkdir()
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
        if serial is not None:
            (d / "serial").write_text(serial + "\n")

    def run_script(self, *args):
        env = {
            "PATH": os.environ["PATH"],
            "USB_SYSFS": str(self.usb),
            "UDEV_RULES_DIR": str(self.rules),
        }
        return subprocess.run(["bash", str(SCRIPT), *args], text=True, env=env, capture_output=True)

    def test_names_the_bootstrap_command_and_exits_zero(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipping driver-autoterm", r.stdout)
        self.assertIn("bin/bootstrap-pi.sh --with-heater", r.stdout)
        self.assertNotIn("serial", r.stdout)

    def test_non_default_host_is_in_the_command(self):
        r = self.run_script("van-pi")
        self.assertIn("bin/bootstrap-pi.sh --host van-pi --with-heater", r.stdout)

    def test_lone_unmapped_adapter_serial_is_shown(self):
        self.add_usb("1-1", "0403", "6001", "A1B2C3")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("A1B2C3", r.stdout)

    def test_adapter_already_in_the_rule_is_not_hinted(self):
        self.add_usb("1-1", "0403", "6001", "A1B2C3")
        (self.rules / RULE_NAME).write_text('... ATTRS{serial}=="A1B2C3", SYMLINK+="ttyAUTOTERM"\n')
        r = self.run_script()
        self.assertNotIn("A1B2C3", r.stdout)

    def test_several_unmapped_adapters_are_not_guessed(self):
        self.add_usb("1-1", "0403", "6001", "AAA")
        self.add_usb("1-2", "0403", "6001", "BBB")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("AAA", r.stdout)
        self.assertNotIn("BBB", r.stdout)

    def test_ignores_non_ftdi_and_serialless_devices(self):
        self.add_usb("1-1", "1d6b", "0002", "hubserial")
        self.add_usb("1-2", "0403", "6001", None)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("hubserial", r.stdout)
        self.assertNotIn("serial ", r.stdout)


if __name__ == "__main__":
    unittest.main()
