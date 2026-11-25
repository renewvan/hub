"""
Tests for the hub plugin: the status payload and what each shared setting declares.

paho.mqtt is a network dependency unavailable on a dev machine; stub it before
importing hub_status so this runs anywhere.

Run: python3 -m unittest plugins.hub.test_hub_status -v
     (or: cd plugins/hub && python3 -m unittest test_hub_status -v)
"""

import importlib.util
import json
import os
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _stub_paho() -> None:
    if "paho.mqtt.client" in sys.modules:
        return
    paho = types.ModuleType("paho")
    paho_mqtt = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")

    class _Client:
        def __init__(self, *a, **k):
            pass

    class _CallbackAPIVersion:
        VERSION2 = 2

    client_mod.Client = _Client
    client_mod.CallbackAPIVersion = _CallbackAPIVersion
    paho_mqtt.client = client_mod
    sys.modules["paho"] = paho
    sys.modules["paho.mqtt"] = paho_mqtt
    sys.modules["paho.mqtt.client"] = client_mod


_stub_paho()

import hub_status  # noqa: E402
import winter_mode  # noqa: E402
import fleet  # noqa: E402
from shared_setting import SharedSetting  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "schema" / "hub-status.schema.json").read_text())


class _FakeSocket:
    def __init__(self, source: str | None, fail: bool = False):
        self._source = source
        self._fail = fail
        self.sent = False
        self.closed = False

    def connect(self, addr):
        if self._fail:
            raise OSError("Network is unreachable")

    def getsockname(self):
        return (self._source, 12345)

    def sendto(self, *a):  # a probe must never transmit
        self.sent = True

    def send(self, *a):
        self.sent = True

    def close(self):
        self.closed = True


class DefaultRouteIpTests(unittest.TestCase):
    def test_returns_the_default_routes_source_address(self):
        sock = _FakeSocket("192.168.10.50")
        with mock.patch.object(hub_status.socket, "socket", return_value=sock):
            self.assertEqual(hub_status.default_route_ip(), "192.168.10.50")

    def test_no_route_is_none_not_an_exception(self):
        sock = _FakeSocket(None, fail=True)
        with mock.patch.object(hub_status.socket, "socket", return_value=sock):
            self.assertIsNone(hub_status.default_route_ip())

    def test_probe_sends_nothing_and_always_closes_the_socket(self):
        for fail in (False, True):
            sock = _FakeSocket("10.0.0.2", fail=fail)
            with mock.patch.object(hub_status.socket, "socket", return_value=sock):
                hub_status.default_route_ip()
            self.assertFalse(sock.sent)
            self.assertTrue(sock.closed)


class HubVersionTests(unittest.TestCase):
    def _stamp(self, text: str | None):
        """Point VERSION_FILE at a temp file holding `text`; None leaves it missing."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "VERSION"
        if text is not None:
            path.write_text(text)
        patcher = mock.patch.object(hub_status, "VERSION_FILE", path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_the_stamp_deploy_wrote_without_its_trailing_newline(self):
        self._stamp("v0.4.0-3-g6a77a25-dirty\n")
        self.assertEqual(hub_status.hub_version(), "v0.4.0-3-g6a77a25-dirty")

    def test_no_stamp_is_dev(self):
        self._stamp(None)
        self.assertEqual(hub_status.hub_version(), "dev")

    def test_blank_stamp_is_dev_because_the_schema_forbids_an_empty_version(self):
        self._stamp("  \n")
        self.assertEqual(hub_status.hub_version(), "dev")


class HubStatusPayloadTests(unittest.TestCase):
    def test_payload_has_exactly_the_schema_fields(self):
        with (
            mock.patch.object(hub_status, "default_route_ip", return_value="192.168.10.50"),
            mock.patch.object(hub_status.socket, "gethostname", return_value="renewvan"),
            mock.patch.object(hub_status, "hub_version", return_value="v0.4.0"),
        ):
            payload = hub_status.hub_status()
        self.assertEqual(
            payload, {"ip": "192.168.10.50", "hostname": "renewvan", "version": "v0.4.0"}
        )
        self.assertEqual(set(payload), set(SCHEMA["properties"]))
        self.assertEqual(set(payload), set(SCHEMA["required"]))

    def test_no_route_still_publishes_the_hostname_and_version(self):
        with (
            mock.patch.object(hub_status, "default_route_ip", return_value=None),
            mock.patch.object(hub_status.socket, "gethostname", return_value="renewvan"),
            mock.patch.object(hub_status, "hub_version", return_value="dev"),
        ):
            self.assertEqual(
                hub_status.hub_status(),
                {"ip": None, "hostname": "renewvan", "version": "dev"},
            )

    def test_empty_hostname_is_null_because_the_schema_forbids_an_empty_string(self):
        with (
            mock.patch.object(hub_status, "default_route_ip", return_value="10.0.0.2"),
            mock.patch.object(hub_status.socket, "gethostname", return_value=""),
        ):
            self.assertIsNone(hub_status.hub_status()["hostname"])

    def test_real_lookup_on_this_machine_returns_an_ipv4_or_none(self):
        ip = hub_status.default_route_ip()
        if ip is not None:
            socket.inet_aton(ip)  # raises on a non-IPv4 string


class _FakeClient:
    """Records what the plugin subscribes to and publishes."""

    def __init__(self):
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str, bool]] = []

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def _msg(topic: str, payload) -> types.SimpleNamespace:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return types.SimpleNamespace(topic=topic, payload=raw)


class _Settings(unittest.TestCase):
    """Every setting's file in a fresh temp directory (that does not exist yet)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for setting in hub_status.SETTINGS:
            path = Path(tmp.name) / "state" / f"{setting.name}.json"
            patcher = mock.patch.object(setting, "path", path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = _FakeClient()

    def send(self, setting, payload):
        hub_status.on_message(self.client, None, _msg(setting.set_topic, payload))

    def state(self, setting):
        """The last value published on the setting's state topic (retained), or None."""
        published = [p for p in self.client.published if p[0] == setting.topic]
        if not published:
            return None
        self.assertTrue(published[-1][2], "state is retained")
        return json.loads(published[-1][1])

    def store(self, setting, raw: bytes):
        setting.path.parent.mkdir(parents=True, exist_ok=True)
        setting.path.write_bytes(raw)

    def assert_ignored(self, setting, payloads, kept):
        """Each payload is ignored: nothing is published and the stored value stays `kept`."""
        self.client.published.clear()
        for payload in payloads:
            with self.subTest(payload=payload):
                self.send(setting, payload)
        self.assertEqual(self.client.published, [])
        self.assertEqual(setting.load(), kept)


class SettingsWiringTests(_Settings):
    """The plugin's part: which settings exist, and that MQTT reaches them. Their lifecycle is
    tested once, in test_shared_setting.py."""

    def test_the_settings_and_their_topics_follow_the_contract(self):
        self.assertEqual(
            [(s.topic, s.set_topic) for s in hub_status.SETTINGS],
            [
                (f"renewvan/hub/{name}", f"renewvan/hub/{name}/set")
                for name in (
                    "primary-battery",
                    "icons",
                    "relay-groups",
                    "relay-group-list",
                    "relay-order",
                )
            ],
        )

    def test_the_default_files_are_the_documented_ones(self):
        with mock.patch.dict(os.environ, clear=True):
            paths = [
                str(SharedSetting(s.name, s.schema, s.default()).path) for s in hub_status.SETTINGS
            ]
        self.assertEqual(
            paths,
            [
                "/var/lib/renewvan-hub/primary-battery.json",
                "/var/lib/renewvan-hub/icons.json",
                "/var/lib/renewvan-hub/relay-groups.json",
                "/var/lib/renewvan-hub/relay-group-list.json",
                "/var/lib/renewvan-hub/relay-order.json",
            ],
        )

    def test_connect_subscribes_and_publishes_every_setting_retained(self):
        hub_status.on_connect(self.client, None, {}, 0)
        settings = len(hub_status.SETTINGS)
        self.assertEqual(
            self.client.subscribed[:settings], [s.set_topic for s in hub_status.SETTINGS]
        )
        self.assertEqual(
            [(p[0], p[2]) for p in self.client.published],
            [(s.topic, True) for s in hub_status.SETTINGS] + [(winter_mode.STATE_TOPIC, True)],
        )

    def test_connect_also_starts_winter_mode(self):
        hub_status.on_connect(self.client, None, {}, 0)
        self.assertIn(winter_mode.SET_TOPIC, self.client.subscribed)
        self.assertIn((winter_mode.STATE_TOPIC, "false", True), self.client.published)

    def test_start_winter_mode_reaches_winter_mode(self):
        with mock.patch.object(hub_status.WINTER_MODE, "message") as message:
            hub_status.on_message(self.client, None, _msg(winter_mode.SET_TOPIC, True))
        message.assert_called_once()

    def test_failed_connect_publishes_and_subscribes_nothing(self):
        hub_status.on_connect(self.client, None, {}, 5)
        self.assertEqual(self.client.subscribed, [])
        self.assertEqual(self.client.published, [])

    def test_a_command_reaches_only_its_own_setting(self):
        commands = {
            hub_status.PRIMARY_BATTERY: "512",
            hub_status.ICONS: {"key": "tank/fresh", "icon": "Droplet"},
            hub_status.RELAY_GROUPS: {"id": "relay-x8-01", "group": "Lights"},
            hub_status.RELAY_GROUP_LIST: ["Lights"],
            hub_status.RELAY_ORDER: ["a"],
        }
        for setting, payload in commands.items():
            with self.subTest(setting=setting.name):
                self.client.published.clear()
                self.send(setting, payload)
                self.assertEqual({p[0] for p in self.client.published}, {setting.topic})

    def test_other_topics_are_ignored(self):
        for topic in ("renewvan/hub/status", "renewvan/hub/primary-battery", "renewvan/x/set"):
            hub_status.on_message(self.client, None, _msg(topic, "512"))
        self.assertEqual(self.client.published, [])
        self.assertFalse(any(s.path.exists() for s in hub_status.SETTINGS))

    def test_status_payload_is_untouched_by_the_settings(self):
        self.assertEqual(set(hub_status.hub_status()), {"ip", "hostname", "version"})

    def test_the_broker_lets_the_hub_write_exactly_its_state_topics_and_command_their_sets(self):
        # bin/broker-clients.conf is the broker's list of who writes what (ADR 0025)
        spec = importlib.util.spec_from_file_location(
            "broker_clients", REPO / "bin" / "broker_clients.py"
        )
        broker_clients = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(broker_clients)
        writes = {c.key: c.writes for c in broker_clients.load()[0]}
        self.assertEqual(
            writes["hub"],
            [
                hub_status.TOPIC_STATUS,
                *(s.topic for s in hub_status.SETTINGS),
                fleet.TOPIC,
                winter_mode.STATE_TOPIC,
                winter_mode.BOARD_SET_FILTER,
            ],
        )
        self.assertEqual(
            [t for t in writes["command"] if t.startswith("renewvan/hub/")],
            [*(s.set_topic for s in hub_status.SETTINGS), winter_mode.SET_TOPIC],
        )
        # Only the hub plugin may tell a board to cut Hub power (ADR 0033).
        self.assertFalse(
            any(
                t.endswith("/winter-mode/set") and t != winter_mode.SET_TOPIC
                for t in writes["command"]
            )
        )

    def test_deploy_ships_the_schemas_the_settings_are_checked_against(self):
        deploy = (REPO / "bin" / "deploy.sh").read_text()
        self.assertIn("schema/hub-*.schema.json", deploy)
        self.assertIn("/opt/renewvan/plugins/hub/schema/", deploy)


class PrimaryBatteryTests(_Settings):
    setting = hub_status.PRIMARY_BATTERY

    def test_default_is_auto(self):
        hub_status.on_connect(self.client, None, {}, 0)
        self.assertEqual(self.state(self.setting), "auto")

    def test_an_instance_id_or_auto_is_a_valid_command(self):
        self.send(self.setting, "512")
        self.assertEqual(self.setting.path.read_text(), '"512"')
        self.assertEqual(self.state(self.setting), "512")
        self.send(self.setting, "auto")
        self.assertEqual(self.state(self.setting), "auto")

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        self.send(self.setting, "512")
        invalid = [
            b"",  # empty payload
            b'""',  # empty string
            b"512",  # number
            b"true",
            b"null",
            b'["512"]',
            b'{"id": "512"}',
            b"512abc",  # not JSON
            b"auto",  # bare word, not a JSON string
            b"\xff\xfe",  # not UTF-8
        ]
        self.assert_ignored(self.setting, invalid, "512")

    def test_unreadable_persisted_value_falls_back_to_auto(self):
        for bad in (b"", b"not json", b"512", b'""', b"\xff\xfe"):
            with self.subTest(bad=bad):
                self.store(self.setting, bad)
                self.assertEqual(self.setting.load(), "auto")


class EntityIconsTests(_Settings):
    setting = hub_status.ICONS

    def _set(self, key, icon):
        self.send(self.setting, {"key": key, "icon": icon})

    def test_default_is_an_empty_map(self):
        hub_status.on_connect(self.client, None, {}, 0)
        self.assertEqual(self.state(self.setting), {})

    def test_a_command_sets_one_entry_and_republishes_the_whole_map(self):
        self._set("temperature/outdoor", "Park")
        self._set("tank/fresh", "Droplet")
        expected = {"temperature/outdoor": "Park", "tank/fresh": "Droplet"}
        self.assertEqual(json.loads(self.setting.path.read_text()), expected)
        self.assertEqual(self.state(self.setting), expected)

    def test_a_later_command_replaces_the_entry_and_null_clears_it(self):
        self._set("tank/fresh", "Droplet")
        self._set("tank/grey", "Waves")
        self._set("tank/fresh", "Waves")
        self.assertEqual(self.setting.load(), {"tank/fresh": "Waves", "tank/grey": "Waves"})
        self._set("tank/fresh", None)
        self.assertEqual(self.setting.load(), {"tank/grey": "Waves"})

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        self._set("tank/fresh", "Droplet")
        invalid = [
            b"",
            b"not json",
            b"\xff\xfe",
            b'"tank/fresh"',
            b"[]",
            {"key": "tank/fresh"},  # no icon
            {"icon": "Droplet"},  # no key
            {"key": "tank/fresh", "icon": "Droplet", "extra": 1},
            {"key": "fresh", "icon": "Droplet"},  # not <entity>/<id>
            {"key": "Tank/fresh", "icon": "Droplet"},  # entity is lower case
            {"key": "tank/fresh/x", "icon": "Droplet"},  # too many segments
            {"key": "tank/fr esh", "icon": "Droplet"},
            {"key": 3, "icon": "Droplet"},
            {"key": "tank/fresh", "icon": "droplet"},  # not PascalCase
            {"key": "tank/fresh", "icon": "not an icon"},
            {"key": "tank/fresh", "icon": ""},
            {"key": "tank/fresh", "icon": 3},
        ]
        self.assert_ignored(self.setting, invalid, {"tank/fresh": "Droplet"})

    def test_unreadable_persisted_map_falls_back_to_empty(self):
        for bad in (b"", b"not json", b'"x"', b"[]", b"\xff\xfe"):
            with self.subTest(bad=bad):
                self.store(self.setting, bad)
                self.assertEqual(self.setting.load(), {})

    def test_one_bad_stored_entry_does_not_cost_the_others(self):
        self.store(
            self.setting,
            b'{"tank/fresh": "Droplet", "bad": "Waves", "tank/grey": "waves", "tank/black": 3}',
        )
        self.assertEqual(self.setting.load(), {"tank/fresh": "Droplet"})


class RelayGroupsTests(_Settings):
    setting = hub_status.RELAY_GROUPS

    def _set(self, relay_id, group):
        self.send(self.setting, {"id": relay_id, "group": group})

    def test_default_is_an_empty_map(self):
        hub_status.on_connect(self.client, None, {}, 0)
        self.assertEqual(self.state(self.setting), {})

    def test_a_command_sets_one_entry_and_republishes_the_whole_map(self):
        self._set("relay-x8-01", "Lights")
        self._set("relay-x8-02", "USB")
        expected = {"relay-x8-01": "Lights", "relay-x8-02": "USB"}
        self.assertEqual(json.loads(self.setting.path.read_text()), expected)
        self.assertEqual(self.state(self.setting), expected)

    def test_a_later_command_replaces_the_entry_and_null_clears_it(self):
        self._set("relay-x8-01", "Lights")
        self._set("relay-x8-02", "USB")
        self._set("relay-x8-01", "Appliances")
        self.assertEqual(self.setting.load(), {"relay-x8-01": "Appliances", "relay-x8-02": "USB"})
        self._set("relay-x8-01", None)
        self.assertEqual(self.setting.load(), {"relay-x8-02": "USB"})

    def test_a_name_may_have_spaces_inside_and_be_32_characters_long(self):
        self._set("relay-x8-01", "Water pump")
        self._set("relay-x8-02", "x" * 32)
        self.assertEqual(
            self.setting.load(), {"relay-x8-01": "Water pump", "relay-x8-02": "x" * 32}
        )

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        self._set("relay-x8-01", "Lights")
        invalid = [
            b"",
            b"not json",
            b"\xff\xfe",
            b'"relay-x8-01"',
            b"[]",
            {"id": "relay-x8-01"},  # no group
            {"group": "Lights"},  # no id
            {"id": "relay-x8-01", "group": "Lights", "extra": 1},
            {"id": "", "group": "Lights"},
            {"id": "relay/01", "group": "Lights"},  # a topic separator
            {"id": "relay 01", "group": "Lights"},
            {"id": "relay+", "group": "Lights"},
            {"id": 3, "group": "Lights"},
            {"id": "relay-x8-01", "group": ""},
            {"id": "relay-x8-01", "group": " Lights"},  # leading space
            {"id": "relay-x8-01", "group": "Lights "},
            {"id": "relay-x8-01", "group": "Lights\n"},
            {"id": "relay-x8-01", "group": "x" * 33},
            {"id": "relay-x8-01", "group": 3},
        ]
        self.assert_ignored(self.setting, invalid, {"relay-x8-01": "Lights"})

    def test_unreadable_persisted_map_falls_back_to_empty(self):
        for bad in (b"", b"not json", b'"x"', b"[]", b"\xff\xfe"):
            with self.subTest(bad=bad):
                self.store(self.setting, bad)
                self.assertEqual(self.setting.load(), {})

    def test_one_bad_stored_entry_does_not_cost_the_others(self):
        self.store(
            self.setting,
            b'{"relay-x8-01": "Lights", "bad/id": "USB", "relay-x8-02": "", "relay-x8-03": 3}',
        )
        self.assertEqual(self.setting.load(), {"relay-x8-01": "Lights"})


class RelayGroupListTests(_Settings):
    setting = hub_status.RELAY_GROUP_LIST

    def test_default_is_the_stock_groups_and_is_not_persisted(self):
        hub_status.on_connect(self.client, None, {}, 0)
        self.assertEqual(
            self.state(self.setting), ["Lights", "Appliances", "USB", "Water", "Heating"]
        )
        self.assertFalse(self.setting.path.exists())

    def test_a_command_replaces_the_list_in_order(self):
        sent = ["USB", "Lights", "Awning"]
        self.send(self.setting, sent)
        self.assertEqual(json.loads(self.setting.path.read_text()), sent)
        self.assertEqual(self.state(self.setting), sent)

    def test_unassigned_may_sit_anywhere_in_the_list(self):
        self.send(self.setting, ["Unassigned", "Lights"])
        self.assertEqual(self.setting.load(), ["Unassigned", "Lights"])

    def test_an_empty_list_is_a_valid_choice_and_is_not_replaced_by_the_stock_groups(self):
        self.send(self.setting, [])
        self.assertEqual(self.setting.load(), [])

    def test_sixteen_groups_are_allowed(self):
        names = [f"Group {i}" for i in range(16)]
        self.send(self.setting, names)
        self.assertEqual(self.setting.load(), names)

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        self.send(self.setting, ["Lights"])
        invalid = [
            b"",
            b"not json",
            b"\xff\xfe",
            b'"Lights"',
            b"{}",
            ["Lights", "Lights"],  # duplicate
            ["Lights", ""],
            ["Lights", " USB"],
            ["Lights", "x" * 33],
            ["Lights", 3],
            ["Lights", None],
            [{"name": "Lights"}],  # an entry, not a name
            [["Lights"]],
            [f"Group {i}" for i in range(17)],  # one too many
        ]
        self.assert_ignored(self.setting, invalid, ["Lights"])

    def test_unreadable_persisted_list_falls_back_to_the_stock_groups(self):
        for bad in (
            b"",
            b"not json",
            b'"x"',
            b"{}",
            b"\xff\xfe",
            b'["a", "a"]',
            b'[{"name": "a"}]',
            b'[""]',
        ):
            with self.subTest(bad=bad):
                self.store(self.setting, bad)
                self.assertEqual(self.setting.load(), hub_status.DEFAULT_RELAY_GROUP_LIST)


class RelayOrderTests(_Settings):
    setting = hub_status.RELAY_ORDER

    def test_default_is_an_empty_order(self):
        hub_status.on_connect(self.client, None, {}, 0)
        self.assertEqual(self.state(self.setting), [])

    def test_a_command_replaces_the_whole_order(self):
        self.send(self.setting, ["relay-x8-02", "relay-x8-01"])
        self.assertEqual(self.state(self.setting), ["relay-x8-02", "relay-x8-01"])
        self.send(self.setting, ["b"])
        self.assertEqual(json.loads(self.setting.path.read_text()), ["b"])

    def test_512_relays_are_allowed_and_513_are_not(self):
        ids = [f"relay-{i}" for i in range(512)]
        self.send(self.setting, ids)
        self.assertEqual(self.setting.load(), ids)
        self.send(self.setting, [*ids, "one-more"])
        self.assertEqual(self.setting.load(), ids)

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        self.send(self.setting, ["a", "b"])
        invalid = [
            b"",
            b"not json",
            b"\xff\xfe",
            b'"a"',
            b"{}",
            ["a", "a"],  # duplicate
            ["a", ""],
            ["a", "b/c"],
            ["a", "b c"],
            ["a", "b+"],
            ["a", 3],
            ["a", None],
            [["a"]],
        ]
        self.assert_ignored(self.setting, invalid, ["a", "b"])

    def test_unreadable_persisted_order_falls_back_to_empty(self):
        for bad in (b"", b"not json", b'"x"', b"{}", b"\xff\xfe", b'["a", "a"]'):
            with self.subTest(bad=bad):
                self.store(self.setting, bad)
                self.assertEqual(self.setting.load(), [])


if __name__ == "__main__":
    unittest.main()
