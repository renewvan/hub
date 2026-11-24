"""Tests for bin/remove_driver.py: what a removed Driver leaves on the bus, and what the tool
clears, refuses and reports. The broker side (mosquitto_sub/mosquitto_pub) is not exercised here;
everything broker-shaped is a plain {topic: payload} dict."""

import json
import unittest
from pathlib import Path

import remove_driver


def info_payload(**overrides):
    payload = {
        "driver": "venus",
        "product": "Venus OS",
        "source": "Cerbo GX",
        "publishes": ["battery", "solar"],
        "version": "v1.0.0",
    }
    payload.update(overrides)
    return json.dumps(payload)


RETAINED = {
    "renewvan/venus/info": info_payload(),
    "renewvan/venus/health": "offline",
    "renewvan/venus/winter-mode": "false",
    "renewvan/battery/house/driver": '"venus"',
    "renewvan/battery/house/soc_pct": "81",
    "renewvan/battery/house/name": '"House"',
    "renewvan/solar/roof/driver": '"venus"',
    "renewvan/solar/roof/power_w": "120",
    "renewvan/battery/starter/driver": '"vedirect"',
    "renewvan/battery/starter/voltage_v": "12.6",
    "renewvan/hub/status": '{"ip":"192.168.1.2","hostname":"van","version":"v1"}',
    "renewvan/tank/fresh/level_pct": "55",  # no driver topic: unattributed
    "renewvan/relay/front-01/driver": '"esp32-front"',
}


class ValidateKeyTest(unittest.TestCase):
    def test_accepts_slugs(self):
        for key in ("venus", "tank", "esp32-front", "esp32-front-left", "a"):
            self.assertEqual(remove_driver.validate_key(key), [])

    def test_rejects_reserved_and_malformed(self):
        for key in ("demo", "hub", "driver-venus", "Venus", "ven_us", "-venus", ""):
            self.assertTrue(remove_driver.validate_key(key), key)


class NamespaceTopicsTest(unittest.TestCase):
    def test_picks_info_health_and_winter_mode(self):
        self.assertEqual(
            remove_driver.namespace_topics("venus", RETAINED),
            ["renewvan/venus/health", "renewvan/venus/info", "renewvan/venus/winter-mode"],
        )

    def test_ignores_longer_keys_and_hub(self):
        topics = {"renewvan/venus-extra/info": "{}", "renewvan/hub/status": "{}"}
        self.assertEqual(remove_driver.namespace_topics("venus", topics), [])


class AttributedInstancesTest(unittest.TestCase):
    def test_parses_json_string_payloads(self):
        self.assertEqual(
            remove_driver.attributed_instances("venus", RETAINED),
            {("battery", "house"), ("solar", "roof")},
        )

    def test_other_drivers_and_junk_are_ignored(self):
        topics = {
            "renewvan/battery/starter/driver": '"vedirect"',
            "renewvan/gps/main/driver": "not json",
            "renewvan/tilt/van/driver": '{"x": 1}',
        }
        self.assertEqual(remove_driver.attributed_instances("venus", topics), set())

    def test_demo_namespace_does_not_match(self):
        topics = {"renewvan/demo/battery/house/driver": '"venus"'}
        self.assertEqual(remove_driver.attributed_instances("venus", topics), set())


class InstanceTopicsTest(unittest.TestCase):
    def test_every_topic_of_the_instance(self):
        self.assertEqual(
            remove_driver.instance_topics("battery", "house", RETAINED),
            [
                "renewvan/battery/house/driver",
                "renewvan/battery/house/name",
                "renewvan/battery/house/soc_pct",
            ],
        )


class TopicsToClearTest(unittest.TestCase):
    def test_namespace_plus_attributed_instances(self):
        self.assertEqual(
            remove_driver.topics_to_clear("venus", RETAINED),
            [
                "renewvan/battery/house/driver",
                "renewvan/battery/house/name",
                "renewvan/battery/house/soc_pct",
                "renewvan/solar/roof/driver",
                "renewvan/solar/roof/power_w",
                "renewvan/venus/health",
                "renewvan/venus/info",
                "renewvan/venus/winter-mode",
            ],
        )

    def test_nothing_for_an_unknown_driver(self):
        self.assertEqual(remove_driver.topics_to_clear("wattcycle", RETAINED), [])


class UnattributedInstancesTest(unittest.TestCase):
    def test_instances_of_a_published_type_with_no_driver_topic(self):
        publishes = json.loads(RETAINED["renewvan/venus/info"])["publishes"] + ["tank"]
        self.assertEqual(
            remove_driver.unattributed_instances(publishes, RETAINED),
            [("tank", "fresh")],
        )

    def test_instances_owned_elsewhere_are_not_unattributed(self):
        publishes = ["battery", "relay"]
        self.assertEqual(remove_driver.unattributed_instances(publishes, RETAINED), [])


class GuardsTest(unittest.TestCase):
    def test_is_online(self):
        self.assertFalse(remove_driver.is_online("venus", RETAINED))
        self.assertTrue(
            remove_driver.is_online("venus", {**RETAINED, "renewvan/venus/health": "online"})
        )
        self.assertFalse(remove_driver.is_online("wattcycle", RETAINED))

    def test_is_online_tolerates_quoted_payload(self):
        topics = {"renewvan/venus/health": '"online"'}
        self.assertTrue(remove_driver.is_online("venus", topics))

    def test_has_hub_power(self):
        info = info_payload(driver="esp32-front", hub_power="front-01")
        topics = {"renewvan/esp32-front/info": info}
        self.assertTrue(remove_driver.has_hub_power("esp32-front", topics))
        self.assertFalse(remove_driver.has_hub_power("venus", RETAINED))
        junk = {"renewvan/wattcycle/info": "junk"}
        self.assertFalse(remove_driver.has_hub_power("wattcycle", junk))


class PublishesTest(unittest.TestCase):
    def test_reads_publishes_from_retained_info(self):
        self.assertEqual(remove_driver.publishes("venus", RETAINED), ["battery", "solar"])

    def test_no_info_or_junk_info_gives_no_types(self):
        self.assertEqual(remove_driver.publishes("wattcycle", RETAINED), [])
        self.assertEqual(remove_driver.publishes("venus", {"renewvan/venus/info": "junk"}), [])


class DeployShipsTheToolTest(unittest.TestCase):
    def test_deploy_ships_the_tool_to_the_pi(self):
        # The tool runs on the Pi, so bin/deploy.sh must rsync it there.
        deploy = (Path(__file__).resolve().parent / "deploy.sh").read_text()
        self.assertRegex(deploy, r"rsync -az [^\n]*bin/remove_driver\.py [^\n]*/bin/")


if __name__ == "__main__":
    unittest.main()
