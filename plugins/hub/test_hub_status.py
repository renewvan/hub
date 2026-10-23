"""
Tests for the hub status plugin's payload.

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


if __name__ == "__main__":
    unittest.main()
