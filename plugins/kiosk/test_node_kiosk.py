"""
Tests for node_kiosk's power/set handling — in particular the
remote-sleep-allowed gate, which must block a remote device from sleeping
the display but must never block the host's own screen from sleeping
itself. See README.md "## MQTT" for the documented contract.

evdev and paho.mqtt are hardware/network dependencies unavailable on a dev
machine; stub them before importing node_kiosk so this runs anywhere.

Run: python3 -m unittest plugins.kiosk.test_node_kiosk -v
     (or: cd plugins/kiosk && python3 -m unittest test_node_kiosk -v)
"""

import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _stub_hardware_modules() -> None:
    if "evdev" not in sys.modules:
        sys.modules["evdev"] = types.ModuleType("evdev")
    if "paho.mqtt.client" not in sys.modules:
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
        client_mod.MQTTMessage = type("MQTTMessage", (), {})
        client_mod.MQTTv5 = 5
        paho_mqtt.client = client_mod
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = paho_mqtt
        sys.modules["paho.mqtt.client"] = client_mod


_stub_hardware_modules()
import node_kiosk as nk  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload=None, qos=1, retain=False):
        self.published.append((topic, payload))


class PowerSetGateTests(unittest.TestCase):
    def setUp(self):
        # Real hardware write, stubbed; always succeeds.
        self._orig_set_display = nk.set_display
        nk.set_display = lambda state: True
        self.client = _FakeClient()
        nk._display_off = False
        nk._got_remote_sleep_retained = True

    def tearDown(self):
        nk.set_display = self._orig_set_display

    def test_remote_off_rejected_when_gate_disabled(self):
        """A device with no recent local touch — i.e. a genuinely remote
        sleep request — must still be blocked when the gate is off."""
        nk._remote_sleep_allowed = False
        nk._last_touch_monotonic = time.monotonic() - 3600  # no recent touch

        nk._handle_power_set(self.client, "off")

        self.assertFalse(nk._display_off, "remote off must not apply while gate is disabled")

    def test_host_local_off_allowed_despite_gate_disabled(self):
        """Regression: a sleep request immediately following a physical
        touch on the host screen is host-local and must apply even when the
        gate blocking *remote* sleep is disabled — the gate exists to stop
        a remote device sleeping the screen out from under someone standing
        at it, never to stop the host sleeping itself."""
        nk._remote_sleep_allowed = False
        nk._last_touch_monotonic = time.monotonic()  # just touched locally

        nk._handle_power_set(self.client, "off")

        self.assertTrue(nk._display_off, "host-local off must apply even while the remote gate is disabled")

    def test_host_local_off_grace_window_expires(self):
        """The local-touch grace window is narrow — a stale touch does not
        grant a free pass to sleep the display indefinitely."""
        nk._remote_sleep_allowed = False
        nk._last_touch_monotonic = time.monotonic() - (nk.LOCAL_COMMAND_GRACE_S + 1)

        nk._handle_power_set(self.client, "off")

        self.assertFalse(nk._display_off, "touch older than the grace window must not count as local")

    def test_remote_off_allowed_when_gate_enabled(self):
        """Unchanged baseline: remote off works normally when the gate is
        enabled, regardless of touch recency."""
        nk._remote_sleep_allowed = True
        nk._last_touch_monotonic = time.monotonic() - 3600

        nk._handle_power_set(self.client, "off")

        self.assertTrue(nk._display_off)


if __name__ == "__main__":
    unittest.main()
