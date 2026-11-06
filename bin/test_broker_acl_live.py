"""The generated ACL enforced by a real Mosquitto (hub#162, ADR 0027): per-board accounts.

Skipped when mosquitto and its clients are not installed. No docker: the broker runs on a loopback
port with the ACL that bin/broker-accounts.sh renders and a password file made by mosquitto_passwd.
"""

import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
TOOLS = ("mosquitto", "mosquitto_passwd", "mosquitto_pub", "mosquitto_sub")
PASSWORDS = {"esp32-front": "pw-front", "esp32-rear": "pw-rear", "reader": "pw-reader"}


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
        """Publish as `user`; True when a reader gets it (a denied publish is silent)."""
        sub = subprocess.Popen(
            [
                "mosquitto_sub",
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
                "3",
                "-C",
                "1",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.7)
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
        out, _ = sub.communicate()
        return payload in out

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


if __name__ == "__main__":
    unittest.main()
