"""Tests for tailscale_status.query_tailscale's normalisation.

paho.mqtt is a network dependency unavailable on a dev machine; stub it
before importing tailscale_status so this runs anywhere. subprocess.run
is replaced per test to feed canned `tailscale status --json` output.

Run: cd plugins/tailscale && python3 -m unittest test_tailscale -v
"""

import json
import subprocess
import sys
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
import tailscale_status as ts  # noqa: E402


def _completed(stdout: str, rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["tailscale"], rc, stdout=stdout, stderr="")


RUNNING = {
    "BackendState": "Running",
    "TailscaleIPs": ["fd7a:115c:a1e0:ab12:4843:cd96:6263:d80a", "100.64.0.2"],
    "Self": {"HostName": "van-pi", "DNSName": "van-pi.tailnet.ts.net."},
    "Peer": {
        "p1": {"Online": True},
        "p2": {"Online": False},
        "p3": {"Online": True},
    },
}


class QueryTailscaleTests(unittest.TestCase):
    def _run(self, result=None, exc=None):
        cm = mock.patch.object(ts.subprocess, "run", side_effect=exc, return_value=result)
        return cm

    def test_running_counts_only_online_peers_and_prefers_ipv4(self):
        with self._run(result=_completed(json.dumps(RUNNING))):
            status = ts.query_tailscale()
        self.assertEqual(
            status,
            {
                "enabled": True,
                "connected": True,
                "ip": "100.64.0.2",
                "hostname": "van-pi",
                "peers": 2,
            },
        )

    def test_running_without_ipv4_falls_back_to_first_address(self):
        data = dict(RUNNING, TailscaleIPs=["fd7a::1"])
        with self._run(result=_completed(json.dumps(data))):
            status = ts.query_tailscale()
        self.assertEqual(status["ip"], "fd7a::1")

    def test_hostname_falls_back_to_dns_name_without_trailing_dot(self):
        data = dict(RUNNING, Self={"DNSName": "van-pi.tailnet.ts.net."})
        with self._run(result=_completed(json.dumps(data))):
            status = ts.query_tailscale()
        self.assertEqual(status["hostname"], "van-pi")

    def test_stopped_backend_reports_disabled(self):
        with self._run(result=_completed(json.dumps({"BackendState": "Stopped"}))):
            self.assertEqual(ts.query_tailscale(), ts.DISABLED_PAYLOAD)

    def test_needs_login_backend_reports_disabled(self):
        with self._run(result=_completed(json.dumps({"BackendState": "NeedsLogin"}))):
            self.assertEqual(ts.query_tailscale(), ts.DISABLED_PAYLOAD)

    def test_nonzero_exit_reports_disabled(self):
        with self._run(result=_completed("not logged in", rc=1)):
            self.assertEqual(ts.query_tailscale(), ts.DISABLED_PAYLOAD)

    def test_timeout_reports_disabled(self):
        with self._run(exc=subprocess.TimeoutExpired(["tailscale"], 10)):
            self.assertEqual(ts.query_tailscale(), ts.DISABLED_PAYLOAD)

    def test_missing_binary_reports_disabled(self):
        with self._run(exc=FileNotFoundError()):
            self.assertEqual(ts.query_tailscale(), ts.DISABLED_PAYLOAD)

    def test_invalid_json_reports_disabled(self):
        with self._run(result=_completed("{not json")):
            self.assertEqual(ts.query_tailscale(), ts.DISABLED_PAYLOAD)


if __name__ == "__main__":
    unittest.main()
