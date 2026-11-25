"""Tests for plugins/hub/fleet.py: the retained renewvan/hub/fleet topic the hub plugin maintains
from the Drivers' info topics, and the bus contract version it carries."""

import json
import time
import unittest
from unittest import mock

from fleet import CONTRACT, TOPIC, Fleet

SETTLE_S = 0.05


def info_message(topic, payload=b'{"driver": "venus"}'):
    msg = mock.Mock()
    msg.topic = topic
    msg.payload = payload
    return msg


def published(client):
    """The (topic, payload, kwargs) of every publish call so far."""
    return [(c.args[0], json.loads(c.args[1]), c.kwargs) for c in client.publish.call_args_list]


def settled_fleet():
    """A Fleet whose connect replay window has closed (the settle timer has fired)."""
    client = mock.Mock()
    fleet = Fleet(settle_s=SETTLE_S)
    fleet.connect(client)
    time.sleep(SETTLE_S * 3)
    return fleet, client


class ConnectTest(unittest.TestCase):
    def test_subscribes_but_publishes_nothing_before_the_replay_settles(self):
        client = mock.Mock()
        fleet = Fleet(settle_s=60)
        fleet.connect(client)
        client.subscribe.assert_called_once_with("renewvan/+/info", qos=1)
        client.publish.assert_not_called()

    def test_publishes_the_fleet_once_the_replay_has_settled(self):
        fleet, client = settled_fleet()
        self.assertEqual(
            published(client),
            [(TOPIC, {"contract": CONTRACT, "drivers": []}, {"qos": 1, "retain": True})],
        )

    def test_replay_messages_before_settle_are_in_the_first_publish(self):
        client = mock.Mock()
        fleet = Fleet(settle_s=SETTLE_S)
        fleet.connect(client)
        fleet.message(client, info_message("renewvan/venus/info"))
        client.publish.assert_not_called()
        time.sleep(SETTLE_S * 3)
        self.assertEqual(published(client)[-1][1], {"contract": CONTRACT, "drivers": ["venus"]})

    def test_reconnect_heals_a_fleet_built_before_a_broker_wipe(self):
        fleet, client = settled_fleet()
        fleet.message(client, info_message("renewvan/venus/info"))
        fleet.connect(client)
        time.sleep(SETTLE_S * 3)
        self.assertEqual(published(client)[-1][1], {"contract": CONTRACT, "drivers": []})


class MessageTest(unittest.TestCase):
    def setUp(self):
        self.fleet, self.client = settled_fleet()

    def test_info_adds_the_driver(self):
        self.assertTrue(self.fleet.message(self.client, info_message("renewvan/venus/info")))
        self.assertEqual(published(self.client)[-1][1]["drivers"], ["venus"])

    def test_duplicate_info_does_not_republish(self):
        self.fleet.message(self.client, info_message("renewvan/venus/info"))
        calls = self.client.publish.call_count
        self.fleet.message(self.client, info_message("renewvan/venus/info", b'{"driver":"venus"}'))
        self.assertEqual(self.client.publish.call_count, calls)

    def test_empty_payload_removes_the_driver(self):
        self.fleet.message(self.client, info_message("renewvan/venus/info"))
        self.fleet.message(self.client, info_message("renewvan/tank/info"))
        self.assertTrue(self.fleet.message(self.client, info_message("renewvan/venus/info", b"")))
        self.assertEqual(published(self.client)[-1][1]["drivers"], ["tank"])

    def test_drivers_are_sorted(self):
        self.fleet.message(self.client, info_message("renewvan/venus/info"))
        self.fleet.message(self.client, info_message("renewvan/esp32-front/info"))
        self.fleet.message(self.client, info_message("renewvan/tank/info"))
        self.assertEqual(published(self.client)[-1][1]["drivers"], ["esp32-front", "tank", "venus"])

    def test_ignores_non_info_topics(self):
        calls = self.client.publish.call_count
        self.assertFalse(self.fleet.message(self.client, info_message("renewvan/venus/health")))
        self.assertFalse(self.fleet.message(self.client, info_message("renewvan/hub/status")))
        self.assertEqual(self.client.publish.call_count, calls)

    def test_ignores_the_reserved_keys(self):
        self.assertFalse(self.fleet.message(self.client, info_message("renewvan/demo/info")))
        self.assertFalse(self.fleet.message(self.client, info_message("renewvan/hub/info")))

    def test_ignores_demo_driver_infos(self):
        # renewvan/demo/<driver>/info is one segment deeper and must not match.
        self.assertFalse(self.fleet.message(self.client, info_message("renewvan/demo/venus/info")))


if __name__ == "__main__":
    unittest.main()
