"""
Tests for node_kiosk's power/set handling.

evdev and paho.mqtt are hardware/network dependencies unavailable on a dev
machine; stub them before importing node_kiosk so this runs anywhere.

Run: python3 -m unittest plugins.kiosk.test_node_kiosk -v
     (or: cd plugins/kiosk && python3 -m unittest test_node_kiosk -v)
"""

import sys
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


class PowerSetTests(unittest.TestCase):
    """power/set has no remote-sleep-allowed gate: a sleep/wake command
    applies unconditionally regardless of who sent it. A gate here was
    trivially bypassable anyway (toggle it back on via Settings from the
    same remote device) and sleep no longer has any visible effect on any
    dashboard viewer at all (the dashboard's sleeping overlay is gone
    too, for the same redundant-with-physical-backlight reason), so
    there was nothing left for a gate to usefully protect."""

    def setUp(self):
        self._orig_set_display = nk.set_display
        nk.set_display = lambda state: True
        self.client = _FakeClient()
        nk._display_off = False

    def tearDown(self):
        nk.set_display = self._orig_set_display

    def test_off_applies_unconditionally(self):
        nk._handle_power_set(self.client, "off")
        self.assertTrue(nk._display_off)

    def test_on_applies_unconditionally(self):
        nk._display_off = True
        nk._handle_power_set(self.client, "on")
        self.assertFalse(nk._display_off)

    def test_invalid_payload_ignored(self):
        nk._handle_power_set(self.client, "banana")
        self.assertFalse(nk._display_off, "an invalid payload must not change display state")


if __name__ == "__main__":
    unittest.main()
