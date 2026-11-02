"""
Tests for bin/install-heater-udev.sh, the Pi-side half of
`bin/bootstrap-pi.sh --with-heater`.

The script's outside world is stubbed so this runs on a Mac: sudo, udevadm,
usermod and id are fake executables on PATH that log their calls, and the USB
sysfs tree and the rules directory are temp dirs.

Run: python3 -m unittest bin.test_heater_udev -v
     (or: cd bin && python3 -m unittest test_heater_udev -v)
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "install-heater-udev.sh"
RULE_NAME = "99-renewvan-autoterm.rules"

STUBS = {
    # sudo logs, then runs the rest for real so `sudo tee` writes into the temp dir.
    "sudo": '#!/bin/sh\necho "sudo $*" >> "$STUB_LOG"\nexec "$@"\n',
    "udevadm": '#!/bin/sh\necho "udevadm $*" >> "$STUB_LOG"\n',
    "usermod": '#!/bin/sh\necho "usermod $*" >> "$STUB_LOG"\n',
    "id": '#!/bin/sh\n[ "$1" = "-nG" ] && echo "$FAKE_GROUPS"\nexit 0\n',
}


class HeaterUdevTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.usb = self.root / "usb"
        self.rules = self.root / "rules"
        self.log = self.root / "log"
        for d in (self.bin, self.usb, self.rules):
            d.mkdir()
        self.log.touch()
        for name, body in STUBS.items():
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.groups = "pi adm"

    def add_usb(self, name, vid, pid, serial):
        d = self.usb / name
        d.mkdir()
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
        if serial is not None:
            (d / "serial").write_text(serial + "\n")

    def run_script(self, stdin=""):
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "FAKE_GROUPS": self.groups,
            "USB_SYSFS": str(self.usb),
            "UDEV_RULES_DIR": str(self.rules),
            "HEATER_USER": "alice",
        }
        return subprocess.run(
            ["bash", str(SCRIPT)], input=stdin, text=True, env=env, capture_output=True
        )

    def rule(self):
        return (self.rules / RULE_NAME).read_text()

    def calls(self):
        return self.log.read_text().splitlines()

    def test_single_adapter_is_auto_detected(self):
        self.add_usb("1-1", "0403", "6001", "A1B2C3")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        rule = self.rule()
        self.assertIn('ATTRS{idVendor}=="0403"', rule)
        self.assertIn('ATTRS{idProduct}=="6001"', rule)
        self.assertIn('ATTRS{serial}=="A1B2C3"', rule)
        self.assertIn('SYMLINK+="ttyAUTOTERM"', rule)
        self.assertIn('GROUP="dialout"', rule)

    def test_reloads_udev_and_adds_login_user_to_dialout(self):
        self.add_usb("1-1", "0403", "6001", "A1B2C3")
        self.run_script()
        calls = self.calls()
        self.assertTrue(any(c.startswith("sudo udevadm control --reload-rules") for c in calls))
        self.assertTrue(any(c.startswith("sudo udevadm trigger") for c in calls))
        self.assertIn("sudo usermod -aG dialout alice", calls)

    def test_ignores_non_ftdi_devices(self):
        self.add_usb("1-1", "1d6b", "0002", "hubserial")
        self.add_usb("1-2", "0403", "6001", "A1B2C3")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("A1B2C3", self.rule())
        self.assertNotIn("hubserial", self.rule())

    def test_second_run_changes_nothing(self):
        self.add_usb("1-1", "0403", "6001", "A1B2C3")
        self.run_script()
        self.groups = "pi adm dialout"
        before = self.rule()
        self.log.write_text("")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rule(), before)
        self.assertEqual(self.calls(), [])

    def test_no_adapter_asks_to_plug_in_and_writes_nothing(self):
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("plug", (r.stdout + r.stderr).lower())
        self.assertFalse((self.rules / RULE_NAME).exists())
        self.assertEqual(self.calls(), [])

    def test_several_adapters_ask_and_use_the_chosen_one(self):
        self.add_usb("1-1", "0403", "6001", "AAA111")
        self.add_usb("1-2", "0403", "6001", "BBB222")
        r = self.run_script(stdin="2\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("AAA111", out)
        self.assertIn("BBB222", out)
        self.assertIn("BBB222", self.rule())
        self.assertNotIn("AAA111", self.rule())

    def test_several_adapters_without_an_answer_never_guess(self):
        self.add_usb("1-1", "0403", "6001", "AAA111")
        self.add_usb("1-2", "0403", "6001", "BBB222")
        r = self.run_script(stdin="")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.rules / RULE_NAME).exists())

    def test_several_adapters_invalid_answer_is_rejected(self):
        self.add_usb("1-1", "0403", "6001", "AAA111")
        self.add_usb("1-2", "0403", "6001", "BBB222")
        r = self.run_script(stdin="9\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.rules / RULE_NAME).exists())

    def test_new_adapter_swaps_the_serial(self):
        self.add_usb("1-1", "0403", "6001", "OLD111")
        self.run_script()
        (self.usb / "1-1" / "serial").write_text("NEW222\n")
        self.groups = "pi dialout"
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NEW222", self.rule())
        self.assertNotIn("OLD111", self.rule())

    def test_adapter_without_serial_is_not_usable(self):
        self.add_usb("1-1", "0403", "6001", None)
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.rules / RULE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
