"""
Tests for kiosk's power/set handling.

evdev and paho.mqtt are hardware/network dependencies unavailable on a dev
machine; stub them before importing kiosk so this runs anywhere.

Run: python3 -m unittest plugins.kiosk.test_kiosk -v
     (or: cd plugins/kiosk && python3 -m unittest test_kiosk -v)
"""

import sys
import tempfile
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
import kiosk as nk  # noqa: E402


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


class MessageParsingTests(unittest.TestCase):
    def test_bool_payload_accepts_only_json_booleans(self):
        self.assertIs(nk._parse_bool_payload(b"true"), True)
        self.assertIs(nk._parse_bool_payload(b"false"), False)
        self.assertIsNone(nk._parse_bool_payload(b'"true"'))
        self.assertIsNone(nk._parse_bool_payload(b"1"))
        self.assertIsNone(nk._parse_bool_payload(b"nope"))

    def test_int_payload_rejects_bools_and_floats(self):
        self.assertEqual(nk._parse_int_payload(b"15"), 15)
        self.assertIsNone(nk._parse_int_payload(b"true"), "bool is a subclass of int")
        self.assertIsNone(nk._parse_int_payload(b"1.5"))
        self.assertIsNone(nk._parse_int_payload(b'"15"'))


class BrightnessRescaleTests(unittest.TestCase):
    """Brightness is 0-100 on the wire, rescaled to the panel's native range in sysfs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        device = Path(self._tmp.name)
        (device / "max_brightness").write_text("255")
        (device / "brightness").write_text("0")
        self._orig_device = nk.BRIGHTNESS_DEVICE
        nk.BRIGHTNESS_DEVICE = str(device)
        nk._backlight_device_cache = False  # force re-resolution against the fake sysfs
        self.addCleanup(self._reset)

    def _reset(self):
        nk.BRIGHTNESS_DEVICE = self._orig_device
        nk._backlight_device_cache = False

    def test_set_and_read_rescale_round_trip(self):
        self.assertTrue(nk.set_brightness_pct(50))
        self.assertEqual(Path(nk._resolve_backlight_device(), "brightness").read_text(), "128")
        self.assertEqual(nk.read_brightness_pct(), 50, "round-trips through the native range")

    def test_clamps_out_of_range_writes(self):
        self.assertTrue(nk.set_brightness_pct(120))
        self.assertEqual(Path(nk._resolve_backlight_device(), "brightness").read_text(), "255")


class AutoSleepStateTopicTests(unittest.TestCase):
    """A retained value on the state topic follows the same rule as the
    command path: only the preset durations apply (the wire schema enum)."""

    def setUp(self):
        self.client = _FakeClient()
        self._orig = nk._auto_sleep_timeout_minutes
        self.addCleanup(self._reset)

    def _reset(self):
        nk._auto_sleep_timeout_minutes = self._orig
        nk._got_auto_sleep_timeout_retained = False

    def _state(self, payload):
        msg = types.SimpleNamespace(topic=nk.TOPIC_AUTO_SLEEP_TIMEOUT_STATE, payload=payload)
        nk.on_message(self.client, None, msg)

    def test_valid_choice_applies(self):
        nk._auto_sleep_timeout_minutes = 5
        self._state(b"15")
        self.assertEqual(nk._auto_sleep_timeout_minutes, 15)

    def test_off_enum_value_is_ignored(self):
        nk._auto_sleep_timeout_minutes = 5
        self._state(b"7")
        self.assertEqual(nk._auto_sleep_timeout_minutes, 5, "the set path would reject it too")

    def test_zero_is_ignored(self):
        nk._auto_sleep_timeout_minutes = 5
        self._state(b"0")
        self.assertEqual(nk._auto_sleep_timeout_minutes, 5)


if __name__ == "__main__":
    unittest.main()
