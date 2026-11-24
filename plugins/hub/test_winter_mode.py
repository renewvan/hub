"""
Tests for Winter mode in the hub plugin (docs/adr/0033-winter-mode-and-hub-power.md): the refusals,
the command to the Hub power board, its confirmation, and the order of state and power-off.

Run: python3 -m unittest plugins.hub.test_winter_mode -v
     (or: cd plugins/hub && python3 -m unittest test_winter_mode -v)
"""

import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import winter_mode  # noqa: E402
from winter_mode import WinterMode  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str, bool]] = []

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))


class _FakeScheduler:
    """Records what was scheduled; a test fires or inspects it by hand."""

    def __init__(self):
        self.jobs: list[dict] = []

    def __call__(self, delay, fn):
        job = {"delay": delay, "fn": fn, "cancelled": False}
        self.jobs.append(job)
        return lambda: job.update(cancelled=True)

    def fire(self, delay):
        for job in [j for j in self.jobs if j["delay"] == delay and not j["cancelled"]]:
            job["cancelled"] = True
            job["fn"]()


def _msg(topic, payload, retain=False):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return types.SimpleNamespace(topic=topic, payload=raw, retain=retain)


def _info(driver, hub_power=None):
    info = {
        "driver": driver,
        "product": "p",
        "source": "s",
        "publishes": ["relay"],
        "version": "v1",
    }
    if hub_power:
        info["hub_power"] = hub_power
    return _msg(f"renewvan/{driver}/info", info, retain=True)


class WinterModeTest(unittest.TestCase):
    def setUp(self):
        self.client = _FakeClient()
        self.schedule = _FakeScheduler()
        self.powered_off = 0
        self.wm = WinterMode(power_off=self._power_off, schedule=self.schedule)

    def _power_off(self):
        self.powered_off += 1

    def feed(self, msg):
        return self.wm.message(self.client, msg)

    def start(self, payload=True):
        return self.feed(_msg(winter_mode.SET_TOPIC, payload))

    def state(self):
        """Every retained publish on renewvan/hub/winter-mode, in order."""
        return [
            json.loads(p) for t, p, r in self.client.published if t == winter_mode.STATE_TOPIC and r
        ]

    def board_commands(self):
        return [(t, p, r) for t, p, r in self.client.published if t.endswith("/winter-mode/set")]

    def ready(self):
        """One Hub power board on the bus and the heater off."""
        self.feed(_info("esp32-front", "front-01"))
        self.feed(_msg("renewvan/heater/air2d/phase", "off", retain=True))

    def test_connect_subscribes_and_publishes_false(self):
        self.wm.connect(self.client)
        self.assertIn(winter_mode.SET_TOPIC, self.client.subscribed)
        self.assertIn("renewvan/+/info", self.client.subscribed)
        self.assertIn("renewvan/+/winter-mode", self.client.subscribed)
        self.assertIn("renewvan/heater/+/phase", self.client.subscribed)
        self.assertIn("renewvan/demo/enabled", self.client.subscribed)
        self.assertEqual(self.state(), [False])

    def test_start_sends_the_board_command_and_waits(self):
        self.ready()
        self.assertTrue(self.start())
        self.assertEqual(
            self.board_commands(), [("renewvan/esp32-front/winter-mode/set", "true", False)]
        )
        self.assertEqual(self.state(), [])
        self.assertEqual(self.powered_off, 0)

    def test_the_board_confirms_then_the_state_is_true_then_power_off(self):
        self.ready()
        self.start()
        self.feed(_msg("renewvan/esp32-front/winter-mode", True))
        self.assertEqual(self.state(), [True])
        self.assertEqual(self.powered_off, 0)  # after a grace, so the state reaches the broker
        self.schedule.fire(winter_mode.POWER_OFF_GRACE_S)
        self.assertEqual(self.powered_off, 1)

    def test_no_confirmation_in_time_aborts_with_false_and_no_power_off(self):
        self.ready()
        self.start()
        self.schedule.fire(winter_mode.CONFIRM_TIMEOUT_S)
        self.assertEqual(self.state(), [False])
        self.assertEqual(self.powered_off, 0)
        # A late confirmation no longer counts.
        self.feed(_msg("renewvan/esp32-front/winter-mode", True))
        self.assertEqual(self.state(), [False])

    def test_a_retained_true_is_never_a_confirmation(self):
        self.ready()
        self.start()
        self.feed(_msg("renewvan/esp32-front/winter-mode", True, retain=True))
        self.assertEqual(self.state(), [])

    def test_a_confirmation_from_another_board_does_not_count(self):
        self.ready()
        self.start()
        self.feed(_msg("renewvan/esp32-rear/winter-mode", True))
        self.feed(_msg("renewvan/esp32-front/winter-mode", False))
        self.assertEqual(self.state(), [])

    def test_the_confirmation_cancels_the_timeout(self):
        self.ready()
        self.start()
        self.feed(_msg("renewvan/esp32-front/winter-mode", True))
        self.schedule.fire(winter_mode.CONFIRM_TIMEOUT_S)
        self.assertEqual(self.state(), [True])

    def test_refused_while_the_heater_is_not_off(self):
        self.ready()
        self.feed(_msg("renewvan/heater/air2d/phase", "running", retain=True))
        self.start()
        self.assertEqual(self.board_commands(), [])
        self.assertEqual(self.state(), [False])

    def test_refused_without_a_hub_power_board(self):
        self.feed(_info("esp32-front"))
        self.start()
        self.assertEqual(self.board_commands(), [])
        self.assertEqual(self.state(), [False])

    def test_refused_when_two_boards_claim_hub_power(self):
        self.ready()
        self.feed(_info("esp32-rear", "rear-01"))
        self.start()
        self.assertEqual(self.board_commands(), [])

    def test_a_board_that_stops_claiming_hub_power_is_forgotten(self):
        self.ready()
        self.feed(_info("esp32-rear", "rear-01"))
        self.feed(_info("esp32-rear"))
        self.start()
        self.assertEqual(len(self.board_commands()), 1)

    def test_a_cleared_info_is_forgotten(self):
        self.ready()
        self.feed(_msg("renewvan/esp32-front/info", b"", retain=True))
        self.start()
        self.assertEqual(self.board_commands(), [])

    def test_refused_in_demo_mode(self):
        self.ready()
        self.feed(_msg("renewvan/demo/enabled", True, retain=True))
        self.start()
        self.assertEqual(self.board_commands(), [])

    def test_ignores_anything_but_true(self):
        self.ready()
        for payload in (False, "true", 1, b"not json"):
            self.start(payload)
        self.assertEqual(self.board_commands(), [])
        self.assertEqual(self.state(), [])

    def test_a_second_start_while_waiting_is_ignored(self):
        self.ready()
        self.start()
        self.start()
        self.assertEqual(len(self.board_commands()), 1)

    def test_its_own_state_topic_is_not_a_board(self):
        self.ready()
        self.start()
        self.assertTrue(self.feed(_msg(winter_mode.STATE_TOPIC, True)))
        self.assertEqual(self.state(), [])

    def test_unrelated_topics_are_not_handled(self):
        self.assertFalse(self.feed(_msg("renewvan/hub/icons/set", {})))


if __name__ == "__main__":
    unittest.main()
