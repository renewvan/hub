"""The generated ACL enforced by a real Mosquitto (hub#162, ADR 0027): per-board accounts.

Skipped when mosquitto and its clients are not installed. No docker: the broker runs on a loopback
port with the ACL that bin/broker-accounts.sh renders and a password file made by mosquitto_passwd.
"""

import os
import pty
import select
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
TOOLS = ("mosquitto", "mosquitto_passwd", "mosquitto_pub", "mosquitto_sub")
PASSWORDS = {
    "esp32-front": "pw-front",
    "esp32-rear": "pw-rear",
    "autoterm": "pw-autoterm",
    "command": "pw-command",
    "reader": "pw-reader",
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(all(shutil.which(t) for t in TOOLS), "mosquitto is not installed")
class BoardAclLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        env = d / ".env"
        env.write_text("BROKER_ACL_OPEN=0\nBROKER_ESP32_BOARDS=front rear\n")
        acl = subprocess.run(
            ["bash", "-c", f"ENV_FILE={env}; source {BIN}/broker-accounts.sh; render_acl 0"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        (d / "acl").write_text(acl)
        for i, (user, pw) in enumerate(PASSWORDS.items()):
            create = ["-c"] if i == 0 else []
            subprocess.run(
                ["mosquitto_passwd", "-b", *create, str(d / "passwd"), user, pw],
                check=True,
                capture_output=True,
            )
        cls.port = free_port()
        (d / "mosquitto.conf").write_text(
            f"listener {cls.port} 127.0.0.1\nallow_anonymous false\n"
            f"password_file {d / 'passwd'}\nacl_file {d / 'acl'}\n"
        )
        cls.broker = subprocess.Popen(
            ["mosquitto", "-c", str(d / "mosquitto.conf")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.1).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.broker.terminate()
        cls.broker.wait()
        cls._tmp.cleanup()

    def delivered(self, user, topic, payload="x"):
        """Publish as `user`; True when a reader gets it (a denied publish is silent).

        No fixed sleep: the subscriber runs with -d on a pty (a pipe would block-buffer its
        output until exit), and the publish waits for its SUBACK line, so the subscription is
        attached before anything is sent. A denied publish then only needs a short window (-W)
        for a late delivery to show up; an allowed one ends the subscriber at once (-C 1).
        """
        master, slave = pty.openpty()
        sub = subprocess.Popen(
            [
                "mosquitto_sub",
                "-d",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-u",
                "reader",
                "-P",
                PASSWORDS["reader"],
                "-t",
                topic,
                "-W",
                "2",
                "-C",
                "1",
            ],
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        self.addCleanup(os.close, master)
        self.addCleanup(sub.wait)
        out = self.read_until(master, "received SUBACK", 5)
        self.assertIn("received SUBACK", out, f"subscriber never attached: {out!r}")
        pub = subprocess.run(
            [
                "mosquitto_pub",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-u",
                user,
                "-P",
                PASSWORDS[user],
                "-t",
                topic,
                "-m",
                payload,
            ],
            capture_output=True,
        )
        self.assertEqual(pub.returncode, 0, f"{user} could not connect: {pub.stderr!r}")
        out += self.read_until(master, None, 3)  # to the subscriber's exit (EOF/EIO on the pty)
        return "received PUBLISH" in out

    @staticmethod
    def read_until(fd, marker, timeout):
        """Read the pty until `marker` shows up (None: until EOF) or time runs out."""
        buf, deadline = "", time.time() + timeout
        while time.time() < deadline and (marker is None or marker not in buf):
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:  # EIO: the child closed its end
                break
            if not chunk:
                break
            buf += chunk.decode(errors="replace")
        return buf

    def test_two_boards_connect_with_their_own_credentials(self):
        for user in ("esp32-front", "esp32-rear"):
            self.assertTrue(self.delivered(user, f"renewvan/{user}/health", "online"), user)

    def test_a_board_cannot_use_another_boards_password(self):
        r = subprocess.run(
            [
                "mosquitto_pub",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-u",
                "esp32-front",
                "-P",
                PASSWORDS["esp32-rear"],
                "-t",
                "renewvan/esp32-front/health",
                "-m",
                "x",
            ],
            capture_output=True,
        )
        self.assertNotEqual(r.returncode, 0)

    def test_a_board_cannot_publish_to_another_boards_driver_topics(self):
        for topic in ("health", "info", "anything/deeper"):
            self.assertFalse(self.delivered("esp32-front", f"renewvan/esp32-rear/{topic}"), topic)
            self.assertFalse(self.delivered("esp32-rear", f"renewvan/esp32-front/{topic}"), topic)

    def test_a_board_cannot_publish_to_the_retired_family_namespace(self):
        self.assertFalse(self.delivered("esp32-front", "renewvan/esp32/health"))

    def test_a_board_cannot_switch_a_relay(self):
        self.assertFalse(self.delivered("esp32-front", "renewvan/relay/front-01/set", "true"))

    def test_a_board_publishes_relay_state_for_its_own_channels(self):
        self.assertTrue(self.delivered("esp32-front", "renewvan/relay/front-01/state", "true"))

    def test_autoterm_publishes_heater_state_but_cannot_command_the_heater(self):
        for topic in (
            "renewvan/heater/info",
            "renewvan/heater/health",
            "renewvan/heater/main/phase",
        ):
            self.assertTrue(self.delivered("autoterm", topic), topic)
        for topic in ("mode", "setpoint_c", "power_level", "ventilating", "clear_error"):
            self.assertFalse(self.delivered("autoterm", f"renewvan/heater/main/{topic}/set"), topic)
        self.assertFalse(self.delivered("autoterm", "renewvan/autoterm/health"))

    def test_command_can_still_send_a_heater_command(self):
        self.assertTrue(self.delivered("command", "renewvan/heater/main/mode/set", '"heat"'))


if __name__ == "__main__":
    unittest.main()
