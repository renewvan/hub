"""
Tests for the hub plugin: the status payload and the shared primary battery setting.

paho.mqtt is a network dependency unavailable on a dev machine; stub it before
importing hub_status so this runs anywhere.

Run: python3 -m unittest plugins.hub.test_hub_status -v
     (or: cd plugins/hub && python3 -m unittest test_hub_status -v)
"""

import json
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

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "hub-status.schema.json").read_text()
)


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


def _msg(payload: bytes, topic: str = hub_status.TOPIC_PRIMARY_BATTERY_SET):
    return types.SimpleNamespace(topic=topic, payload=payload)


class PrimaryBatteryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # A directory that does not exist yet: the plugin must create it.
        self.path = Path(tmp.name) / "state" / "primary-battery.json"
        patcher = mock.patch.object(hub_status, "PRIMARY_BATTERY_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _FakeClient()

    def _connect(self):
        hub_status.on_connect(self.client, None, {}, 0)

    def _state_publishes(self):
        return [p for p in self.client.published if p[0] == hub_status.TOPIC_PRIMARY_BATTERY]

    def test_topics_follow_the_contract(self):
        self.assertEqual(hub_status.TOPIC_PRIMARY_BATTERY, "renewvan/hub/primary-battery")
        self.assertEqual(hub_status.TOPIC_PRIMARY_BATTERY_SET, "renewvan/hub/primary-battery/set")

    def test_connect_with_nothing_persisted_publishes_auto_retained(self):
        self._connect()
        self.assertEqual(self.client.subscribed, [hub_status.TOPIC_PRIMARY_BATTERY_SET])
        self.assertEqual(
            self._state_publishes(), [(hub_status.TOPIC_PRIMARY_BATTERY, '"auto"', True)]
        )

    def test_connect_publishes_the_persisted_value_retained(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('"wattcycle"')
        self._connect()
        self.assertEqual(
            self._state_publishes(), [(hub_status.TOPIC_PRIMARY_BATTERY, '"wattcycle"', True)]
        )

    def test_every_reconnect_publishes_again(self):
        self._connect()
        self._connect()
        self.assertEqual(len(self._state_publishes()), 2)
        self.assertEqual(self.client.subscribed.count(hub_status.TOPIC_PRIMARY_BATTERY_SET), 2)

    def test_failed_connect_publishes_and_subscribes_nothing(self):
        hub_status.on_connect(self.client, None, {}, 5)
        self.assertEqual(self.client.subscribed, [])
        self.assertEqual(self.client.published, [])

    def test_unreadable_persisted_value_falls_back_to_auto(self):
        for bad in (b"", b"not json", b"512", b'""', b"\xff\xfe"):
            with self.subTest(bad=bad):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(bad)
                self.assertEqual(hub_status.load_primary_battery(), "auto")

    def test_valid_command_persists_and_republishes_retained(self):
        hub_status.on_message(self.client, None, _msg(b'"512"'))
        self.assertEqual(self.path.read_text(), '"512"')
        self.assertEqual(
            self._state_publishes(), [(hub_status.TOPIC_PRIMARY_BATTERY, '"512"', True)]
        )

    def test_a_persisted_command_survives_a_restart(self):
        hub_status.on_message(self.client, None, _msg(b'"wattcycle"'))
        restarted = _FakeClient()
        hub_status.on_connect(restarted, None, {}, 0)
        self.assertEqual(
            restarted.published, [(hub_status.TOPIC_PRIMARY_BATTERY, '"wattcycle"', True)]
        )

    def test_auto_is_a_valid_command(self):
        hub_status.on_message(self.client, None, _msg(b'"512"'))
        hub_status.on_message(self.client, None, _msg(b'"auto"'))
        self.assertEqual(self.path.read_text(), '"auto"')
        self.assertEqual(self._state_publishes()[-1][1], '"auto"')

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        hub_status.save_primary_battery("512")
        hub_status.save_primary_battery("513")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])
        self.assertEqual(self.path.read_text(), '"513"')

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        hub_status.on_message(self.client, None, _msg(b'"512"'))
        self.client.published.clear()
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
        for payload in invalid:
            with self.subTest(payload=payload):
                hub_status.on_message(self.client, None, _msg(payload))
        self.assertEqual(self.client.published, [])
        self.assertEqual(self.path.read_text(), '"512"')

    def test_invalid_command_with_nothing_persisted_writes_nothing(self):
        hub_status.on_message(self.client, None, _msg(b"512"))
        self.assertFalse(self.path.exists())
        self.assertEqual(self.client.published, [])

    def test_unwritable_state_is_not_applied_or_published(self):
        with mock.patch.object(hub_status, "save_primary_battery", side_effect=OSError("ro")):
            hub_status.on_message(self.client, None, _msg(b'"512"'))
        self.assertEqual(self.client.published, [])

    def test_other_topics_are_ignored(self):
        hub_status.on_message(self.client, None, _msg(b'"512"', topic="renewvan/hub/status"))
        self.assertEqual(self.client.published, [])
        self.assertFalse(self.path.exists())

    def test_status_payload_is_untouched_by_the_setting(self):
        self.assertEqual(set(hub_status.hub_status()), {"ip", "hostname", "version"})


if __name__ == "__main__":
    unittest.main()
