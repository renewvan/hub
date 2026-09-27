#!/usr/bin/env python3
"""
renewvan/node-kiosk — display power bridge.

Subscribes to  renewvan/kiosk/display/power/set  ("on" | "off").
Executes the configured display-power command (default: wlopm --off/--on DSI-1).
Publishes the resulting state retained to  renewvan/kiosk/display/power.

On startup the node probes the display-power command to read the current state
and publishes it as the initial retained value, so the bus always reflects
hardware reality rather than the last command.

Touch-to-wake: a background thread monitors the raw evdev touch device
(TOUCH_DEVICE, default: auto-detected). When a TOUCH_DOWN event arrives while
the display is off, the node publishes "on" to the command topic — same as a
manual wake from the dashboard. This works even under wlopm where Wayland gates
input to Chromium clients when the output is powered off.

If the display-power command is unavailable the node logs an error, publishes
nothing, and continues — the dashboard and broker are unaffected.

Environment variables (all optional):
  MQTT_HOST          MQTT broker hostname          (default: localhost)
  MQTT_PORT          MQTT broker port              (default: 1883)
  MQTT_USERNAME      broker username               (default: empty)
  MQTT_PASSWORD      broker password               (default: empty)
  DISPLAY_ON_CMD     shell command to turn on      (default: wlopm --on DSI-1)
  DISPLAY_OFF_CMD    shell command to turn off     (default: wlopm --off DSI-1)
  DISPLAY_QUERY_CMD  shell command to query state  (default: wlopm)
  TOUCH_DEVICE       evdev device path for touch-to-wake
                     (default: auto-detect first touch-capable device)
"""

import logging
import os
import shlex
import subprocess
import sys
import threading
import time

import evdev
import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")

DISPLAY_ON_CMD = os.environ.get("DISPLAY_ON_CMD", "wlopm --on DSI-1")
DISPLAY_OFF_CMD = os.environ.get("DISPLAY_OFF_CMD", "wlopm --off DSI-1")
DISPLAY_QUERY_CMD = os.environ.get("DISPLAY_QUERY_CMD", "wlopm")
TOUCH_DEVICE = os.environ.get("TOUCH_DEVICE", "")

TOPIC_STATE = "renewvan/kiosk/display/power"
TOPIC_SET = "renewvan/kiosk/display/power/set"

VALID_PAYLOADS = {"on", "off"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [node-kiosk] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Display power helpers
# ---------------------------------------------------------------------------


def _run(cmd: str) -> tuple[bool, str]:
    """Run a shell command. Returns (success, stdout)."""
    try:
        result = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            log.error("Command %r exited %d: %s", cmd, result.returncode, result.stderr.strip())
            return False, ""
        return True, result.stdout.strip()
    except FileNotFoundError:
        log.error("Command not found: %r", cmd)
        return False, ""
    except subprocess.TimeoutExpired:
        log.error("Command timed out: %r", cmd)
        return False, ""
    except Exception as exc:
        log.error("Command %r failed: %s", cmd, exc)
        return False, ""


def query_display_state() -> str | None:
    """
    Query the current display power state.
    Returns "on", "off", or None if unavailable/unreadable.

    Supported output formats:
      bl_power sysfs (Official 7" Touch Display v1): "0" = on, "1" = off
      wlopm: lines like "DSI-1  on" / "DSI-1  off"
      vcgencmd: "display_power=1" (on) / "display_power=0" (off)
    """
    ok, stdout = _run(DISPLAY_QUERY_CMD)
    if not ok:
        return None
    stripped = stdout.strip()
    # bl_power sysfs: raw "0" (backlight on) or "1" (backlight off)
    if stripped == "0":
        return "on"
    if stripped == "1":
        return "off"
    # wlopm: lines ending in " on" / " off"
    for line in stripped.splitlines():
        if line.strip().endswith(" off"):
            return "off"
        if line.strip().endswith(" on"):
            return "on"
    # vcgencmd fallback
    if "display_power=1" in stripped:
        return "on"
    if "display_power=0" in stripped:
        return "off"
    log.warning("Unrecognised display query output: %r", stdout)
    return None


def set_display(state: str) -> bool:
    """Apply display power state. Returns True on success."""
    cmd = DISPLAY_ON_CMD if state == "on" else DISPLAY_OFF_CMD
    ok, _ = _run(cmd)
    return ok


# ---------------------------------------------------------------------------
# Touch device auto-detection
# ---------------------------------------------------------------------------


def find_touch_device() -> str | None:
    """
    Return the evdev path for the first touch-capable input device, or None.
    A touch device has EV_ABS capability and reports ABS_MT_POSITION_X or
    ABS_X (single-touch). The ft5x06 controller on the Official Touch
    Display 2 appears as /dev/input/event* with EV_ABS + BTN_TOUCH.
    """
    for path in sorted(evdev.list_devices(), reverse=True):  # event4 before event0
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            # caps[EV_ABS] is a list of (code, AbsInfo) tuples — extract codes only
            abs_codes = {code for code, _ in caps.get(evdev.ecodes.EV_ABS, [])}
            has_touch = (
                evdev.ecodes.ABS_MT_POSITION_X in abs_codes
                or evdev.ecodes.ABS_X in abs_codes
            )
            dev.close()
            if has_touch:
                return path
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Touch-to-wake thread
# ---------------------------------------------------------------------------


def touch_wake_thread(get_display_off: "callable[[], bool]", on_wake: "callable[[], None]") -> None:
    """
    Monitors the raw evdev touch device. Grabs the device (blocking compositor
    routing) only while the display is sleeping, releases it immediately before
    publishing wake so the waking touch is swallowed but normal touch resumes
    at once. Polls every 100 ms so grab/ungrab tracks display-state changes.
    """
    import select

    path = TOUCH_DEVICE or find_touch_device()
    if not path:
        log.warning("No touch device found — touch-to-wake disabled. Set TOUCH_DEVICE to enable.")
        return

    log.info("Touch-to-wake monitoring %s", path)
    try:
        dev = evdev.InputDevice(path)
    except Exception as exc:
        log.error("Cannot open touch device %s: %s — touch-to-wake disabled", path, exc)
        return

    grabbed = False
    try:
        while True:
            should_grab = get_display_off()

            # Sync grab state with current display state
            if should_grab and not grabbed:
                try:
                    dev.grab()
                    grabbed = True
                    log.info("Grabbed %s (display sleeping — touch events swallowed)", path)
                except Exception as exc:
                    log.warning("Could not grab touch device: %s", exc)
            elif not should_grab and grabbed:
                try:
                    dev.ungrab()
                    grabbed = False
                    log.info("Released %s (display awake — touch flowing normally)", path)
                except Exception:
                    grabbed = False

            # Poll with 100 ms timeout so grab state stays in sync with display state
            r, _, _ = select.select([dev.fileno()], [], [], 0.1)
            if not r:
                continue

            for event in dev.read():
                is_touch = (
                    event.type == evdev.ecodes.EV_ABS
                    and event.code in (evdev.ecodes.ABS_MT_POSITION_X, evdev.ecodes.ABS_X)
                ) or (
                    event.type == evdev.ecodes.EV_KEY
                    and event.code == evdev.ecodes.BTN_TOUCH
                    and event.value == 1
                )
                if is_touch and grabbed:
                    try:
                        dev.ungrab()
                        grabbed = False
                    except Exception:
                        grabbed = False
                    log.info("Touch detected while display sleeping — waking")
                    on_wake()
                    break  # discard remaining events in this read batch
    except Exception as exc:
        log.error("Touch-to-wake reader failed: %s", exc)
    finally:
        if grabbed:
            try:
                dev.ungrab()
            except Exception:
                pass
        try:
            dev.close()
        except Exception:
            pass


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc != 0:
        log.error("MQTT connect failed (rc=%d) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)
    client.subscribe(TOPIC_SET, qos=1)
    log.info("Subscribed to %s", TOPIC_SET)

    state = query_display_state()
    if state is not None:
        global _display_off
        _display_off = state == "off"
        client.publish(TOPIC_STATE, payload=state, qos=1, retain=True)
        log.info("Published initial state: %s", state)
    else:
        log.warning(
            "Could not read initial display state — retained topic not updated. "
            "Check that %r is available on this host.",
            DISPLAY_QUERY_CMD,
        )


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    global _display_off
    try:
        payload = msg.payload.decode().strip().lower()
    except Exception:
        log.warning("Received undecodable payload on %s — ignoring", msg.topic)
        return

    if payload not in VALID_PAYLOADS:
        log.warning("Invalid payload %r on %s — ignoring", payload, msg.topic)
        return

    log.info("Received command: %s", payload)
    if set_display(payload):
        _display_off = payload == "off"
        client.publish(TOPIC_STATE, payload=payload, qos=1, retain=True)
        log.info("Display set to %s — published state", payload)
    else:
        log.error("Failed to set display to %s — state topic not updated", payload)


def on_disconnect(client: mqtt.Client, userdata, rc, properties=None):
    if rc != 0:
        log.warning("Unexpected MQTT disconnect (rc=%d) — paho will reconnect", rc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    log.info("node-kiosk starting (broker=%s:%d)", MQTT_HOST, MQTT_PORT)

    client = mqtt.Client(
        client_id="renewvan-node-kiosk",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv5,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # Reconnect loop: wait up to 60 s for the broker.
    deadline = time.monotonic() + 60
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except OSError as exc:
            if time.monotonic() >= deadline:
                log.error("Broker not reachable after 60 s: %s — giving up", exc)
                sys.exit(1)
            log.info("Broker not yet reachable (%s) — retrying in 5 s", exc)
            time.sleep(5)

    def _on_wake():
        """Called from the touch thread; publishes a wake command via MQTT."""
        log.info("Touch-to-wake: publishing 'on' command")
        client.publish(TOPIC_SET, payload="on", qos=1, retain=False)

    t = threading.Thread(
        target=touch_wake_thread,
        args=(lambda: _display_off, _on_wake),
        daemon=True,
        name="touch-wake",
    )
    t.start()

    client.loop_forever()


if __name__ == "__main__":
    main()
