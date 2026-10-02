#!/usr/bin/env python3
"""
renewvan/node-kiosk — display power bridge, brightness control, remote-sleep
gate, and auto-sleep idle timer.

## Display power

Subscribes to  renewvan/kiosk/display/power/set  ("on" | "off").
Executes the configured display-power command (default: wlopm --off/--on DSI-1).
Publishes the resulting state retained to  renewvan/kiosk/display/power.

On startup the node probes the display-power command to read the current state
and publishes it as the initial retained value, so the bus always reflects
hardware reality rather than the last command.

## Remote-sleep-allowed gate

Subscribes to  renewvan/kiosk/display/remote-sleep-allowed/set  (boolean).
When this gate is false, an incoming "off" command on the power/set topic is
rejected (logged, no-op) — the hardware/host can always be put to sleep by
whoever has shell access, but a remote/browser command cannot unless the gate
is open. Waking ("on") is never gated. Enforced here, not just hidden in the
dashboard UI, since a browser-side-only gate is trivially bypassed by anyone
who can publish MQTT directly. Defaults to true (allowed) if no retained
value exists on startup.

## Brightness

Subscribes to  renewvan/kiosk/display/brightness/set  (integer 0-100,
normalized — the wire contract doesn't encode the attached panel's native
brightness range). Writes the kernel backlight sysfs node
(/sys/class/backlight/<id>/brightness, rescaled to the panel's native
max_brightness) and publishes the resulting state retained. On startup,
publishes the live sysfs reading (rescaled to 0-100), mirroring the
display-power startup probe.

## Auto-sleep idle timer

Subscribes to  renewvan/kiosk/display/auto-sleep-enabled/set  (boolean) and
renewvan/kiosk/display/auto-sleep-timeout-minutes/set  (integer, one of
1/5/15/30). When enabled, the node tracks touch activity on the same evdev
touch device already used for touch-to-wake; once the configured number of
idle minutes elapses with the display on, the node sleeps the display
itself — independent of the remote-sleep-allowed gate (this is a host-local
decision, not a remote command). Both settings default to disabled/5 if no
retained value exists on startup.

## Touch-to-wake

Touch-to-wake: a background thread monitors the raw evdev touch device
(TOUCH_DEVICE, default: auto-detected). When a TOUCH_DOWN event arrives while
the display is off, the node publishes "on" to the command topic — same as a
manual wake from the dashboard. This works even under wlopm where Wayland gates
input to Chromium clients when the output is powered off. Every touch event
(not just while sleeping) also resets the auto-sleep idle clock.

If a display-power/brightness command is unavailable the node logs an error,
publishes nothing for that property, and continues — the dashboard and
broker are unaffected.

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
  BRIGHTNESS_DEVICE  /sys/class/backlight/<id> directory for brightness
                     (default: auto-detect first backlight device)
"""

import json
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
BRIGHTNESS_DEVICE = os.environ.get("BRIGHTNESS_DEVICE", "")

TOPIC_STATE = "renewvan/kiosk/display/power"
TOPIC_SET = "renewvan/kiosk/display/power/set"

TOPIC_REMOTE_SLEEP_ALLOWED_STATE = "renewvan/kiosk/display/remote-sleep-allowed"
TOPIC_REMOTE_SLEEP_ALLOWED_SET = "renewvan/kiosk/display/remote-sleep-allowed/set"

TOPIC_BRIGHTNESS_STATE = "renewvan/kiosk/display/brightness"
TOPIC_BRIGHTNESS_SET = "renewvan/kiosk/display/brightness/set"

TOPIC_AUTO_SLEEP_ENABLED_STATE = "renewvan/kiosk/display/auto-sleep-enabled"
TOPIC_AUTO_SLEEP_ENABLED_SET = "renewvan/kiosk/display/auto-sleep-enabled/set"

TOPIC_AUTO_SLEEP_TIMEOUT_STATE = "renewvan/kiosk/display/auto-sleep-timeout-minutes"
TOPIC_AUTO_SLEEP_TIMEOUT_SET = "renewvan/kiosk/display/auto-sleep-timeout-minutes/set"

VALID_PAYLOADS = {"on", "off"}
AUTO_SLEEP_TIMEOUT_CHOICES = {1, 5, 15, 30}

DEFAULT_REMOTE_SLEEP_ALLOWED = True
DEFAULT_AUTO_SLEEP_ENABLED = False
DEFAULT_AUTO_SLEEP_TIMEOUT_MINUTES = 5

# How long to wait after subscribing for a retained value to arrive before
# concluding none exists and publishing the default. Topics with no hardware
# source of truth (unlike power/brightness, which probe real state) have no
# other way to know "never published" from "published, happens to be default".
RETAINED_PROBE_WINDOW_S = 2.0

AUTO_SLEEP_POLL_INTERVAL_S = 5.0

# A power/set command arriving within this many seconds of the last physical
# touch is treated as host-local (same signal _display_on_origin uses for
# wakes) and bypasses the remote-sleep-allowed gate. power/set carries no
# origin field — the same dashboard code runs whether loaded in the van's
# own kiosk Chromium or on a remote phone, so a touch immediately before the
# command is the only available "this request came from someone physically
# at the van" signal. Generous vs. typical same-LAN tap-to-MQTT latency
# (tens of ms), tight vs. plausible coincidence with an unrelated remote
# command.
LOCAL_COMMAND_GRACE_S = 3.0

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
# Brightness helpers
# ---------------------------------------------------------------------------

_backlight_device_cache: str | None | bool = False  # False = not yet resolved


def find_backlight_device() -> str | None:
    """Return the first /sys/class/backlight/<id> directory, or None."""
    base = "/sys/class/backlight"
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    return os.path.join(base, entries[0]) if entries else None


def _resolve_backlight_device() -> str | None:
    """Resolve and cache the backlight device directory for this process."""
    global _backlight_device_cache
    if _backlight_device_cache is False:
        device = BRIGHTNESS_DEVICE or find_backlight_device()
        if device:
            log.info("Using backlight device %s", device)
        else:
            log.warning("No backlight device found under /sys/class/backlight — brightness control disabled")
        _backlight_device_cache = device
    return _backlight_device_cache


def read_brightness_pct() -> int | None:
    """Read live brightness, rescaled from the panel's native range to 0-100."""
    device = _resolve_backlight_device()
    if not device:
        return None
    try:
        with open(os.path.join(device, "brightness")) as f:
            raw = int(f.read().strip())
        with open(os.path.join(device, "max_brightness")) as f:
            max_raw = int(f.read().strip())
        if max_raw <= 0:
            return None
        return round(raw * 100 / max_raw)
    except (OSError, ValueError) as exc:
        log.error("Failed to read brightness from %s: %s", device, exc)
        return None


def set_brightness_pct(pct: int) -> bool:
    """Write brightness, rescaled from 0-100 to the panel's native range."""
    device = _resolve_backlight_device()
    if not device:
        log.error("No backlight device — cannot set brightness")
        return False
    try:
        with open(os.path.join(device, "max_brightness")) as f:
            max_raw = int(f.read().strip())
        raw = max(0, min(max_raw, round(pct * max_raw / 100)))
        with open(os.path.join(device, "brightness"), "w") as f:
            f.write(str(raw))
        return True
    except (OSError, ValueError, PermissionError) as exc:
        log.error("Failed to set brightness on %s: %s", device, exc)
        return False


# ---------------------------------------------------------------------------
# Boolean/integer payload helpers (JSON scalars, not the power topic's
# "on"/"off" string enum — see schema/kiosk-display-*.schema.json)
# ---------------------------------------------------------------------------


def _parse_bool_payload(raw: bytes) -> bool | None:
    try:
        value = json.loads(raw.decode().strip())
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, bool) else None


def _parse_int_payload(raw: bytes) -> int | None:
    try:
        value = json.loads(raw.decode().strip())
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
# Touch-to-wake thread (also drives the auto-sleep idle clock via on_touch)
# ---------------------------------------------------------------------------


def touch_wake_thread(
    get_display_off: "callable[[], bool]",
    on_wake: "callable[[], None]",
    on_touch: "callable[[], None] | None" = None,
) -> None:
    """
    Monitors the raw evdev touch device. Grabs the device (blocking compositor
    routing) only while the display is sleeping, releases it immediately before
    publishing wake so the waking touch is swallowed but normal touch resumes
    at once. Polls every 100 ms so grab/ungrab tracks display-state changes.

    Every detected touch event calls on_touch (if given), regardless of grab
    state — this is how the auto-sleep idle timer's clock gets reset by
    normal, awake-screen interaction, not just by waking touches.
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
                if not is_touch:
                    continue
                if on_touch:
                    on_touch()
                if grabbed:
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


# ---------------------------------------------------------------------------
# Auto-sleep idle timer thread
# ---------------------------------------------------------------------------


def auto_sleep_thread(
    get_state: "callable[[], tuple[bool, int, float, str]]",
    get_display_off: "callable[[], bool]",
    trigger_sleep: "callable[[], None]",
) -> None:
    """
    Polls every AUTO_SLEEP_POLL_INTERVAL_S. If auto-sleep is enabled, the
    display is currently on, the idle clock (last touch, from
    touch_wake_thread's on_touch callback) has exceeded the configured
    timeout, AND the current on-session originated from a physical touch
    (not a remote wake command), triggers a local sleep.

    The origin check is what makes auto-sleep a host-presence feature
    rather than a display-on-duration timer: a remote wake (phone app,
    dashboard) has no touch signal to measure idleness against, so without
    it the idle clock reads as stale the moment someone starts working
    remotely and the display re-sleeps mid-session. A physical touch always
    flips the origin back to "local", so the feature still protects the
    screen once someone is actually at the van and walks away.

    Independent of the remote-sleep-allowed gate — this is the node
    deciding to sleep itself, not honoring a remote command.
    """
    while True:
        time.sleep(AUTO_SLEEP_POLL_INTERVAL_S)
        enabled, timeout_minutes, last_touch_monotonic, origin = get_state()
        if not enabled or get_display_off() or origin != "local":
            continue
        idle_s = time.monotonic() - last_touch_monotonic
        if idle_s >= timeout_minutes * 60:
            log.info("Auto-sleep: idle for %.0fs (timeout %dm) — sleeping display", idle_s, timeout_minutes)
            trigger_sleep()


# ---------------------------------------------------------------------------
# Module-level state — updated by on_connect/on_message, read by the
# touch-wake and auto-sleep threads.
# ---------------------------------------------------------------------------

_display_off = False
_remote_sleep_allowed = DEFAULT_REMOTE_SLEEP_ALLOWED
_auto_sleep_enabled = DEFAULT_AUTO_SLEEP_ENABLED
_auto_sleep_timeout_minutes = DEFAULT_AUTO_SLEEP_TIMEOUT_MINUTES
_last_touch_monotonic = time.monotonic()

# Provenance of the current on-session: "local" if the display was last
# turned on (or last confirmed occupied) by a physical touch, "remote" if
# by an MQTT power/set=on command with no accompanying touch. Read by
# auto_sleep_thread so idle-based sleep only ever fires for host-local
# sessions — see its docstring. Defaults to "local" so a display already on
# at startup (e.g. after a reboot while someone's at the van) behaves as
# before this field existed.
_display_on_origin = "local"

# Set True the moment a retained message is observed on each "soft" state
# topic (no hardware readback exists for these, unlike power/brightness), so
# the post-subscribe default-publish timer knows whether one already exists.
_got_remote_sleep_retained = False
_got_auto_sleep_enabled_retained = False
_got_auto_sleep_timeout_retained = False


def _publish_missing_defaults(client: mqtt.Client) -> None:
    """
    Runs once, RETAINED_PROBE_WINDOW_S after subscribing. Publishes the
    default for any "soft" setting that never received a retained value —
    same self-healing intent as the display-power startup probe, just
    without a hardware read to fall back on.
    """
    global _remote_sleep_allowed, _auto_sleep_enabled, _auto_sleep_timeout_minutes
    if not _got_remote_sleep_retained:
        _remote_sleep_allowed = DEFAULT_REMOTE_SLEEP_ALLOWED
        client.publish(TOPIC_REMOTE_SLEEP_ALLOWED_STATE, payload=json.dumps(DEFAULT_REMOTE_SLEEP_ALLOWED), qos=1, retain=True)
        log.info("No retained remote-sleep-allowed — published default %s", DEFAULT_REMOTE_SLEEP_ALLOWED)
    if not _got_auto_sleep_enabled_retained:
        _auto_sleep_enabled = DEFAULT_AUTO_SLEEP_ENABLED
        client.publish(TOPIC_AUTO_SLEEP_ENABLED_STATE, payload=json.dumps(DEFAULT_AUTO_SLEEP_ENABLED), qos=1, retain=True)
        log.info("No retained auto-sleep-enabled — published default %s", DEFAULT_AUTO_SLEEP_ENABLED)
    if not _got_auto_sleep_timeout_retained:
        _auto_sleep_timeout_minutes = DEFAULT_AUTO_SLEEP_TIMEOUT_MINUTES
        client.publish(TOPIC_AUTO_SLEEP_TIMEOUT_STATE, payload=json.dumps(DEFAULT_AUTO_SLEEP_TIMEOUT_MINUTES), qos=1, retain=True)
        log.info("No retained auto-sleep-timeout-minutes — published default %s", DEFAULT_AUTO_SLEEP_TIMEOUT_MINUTES)


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    global _display_off
    if rc != 0:
        log.error("MQTT connect failed (rc=%d) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)

    for topic in (
        TOPIC_SET,
        TOPIC_REMOTE_SLEEP_ALLOWED_STATE,
        TOPIC_REMOTE_SLEEP_ALLOWED_SET,
        TOPIC_BRIGHTNESS_SET,
        TOPIC_AUTO_SLEEP_ENABLED_STATE,
        TOPIC_AUTO_SLEEP_ENABLED_SET,
        TOPIC_AUTO_SLEEP_TIMEOUT_STATE,
        TOPIC_AUTO_SLEEP_TIMEOUT_SET,
    ):
        client.subscribe(topic, qos=1)
    log.info("Subscribed to kiosk display command/settings topics")

    state = query_display_state()
    if state is not None:
        _display_off = state == "off"
        client.publish(TOPIC_STATE, payload=state, qos=1, retain=True)
        log.info("Published initial power state: %s", state)
    else:
        log.warning(
            "Could not read initial display state — retained topic not updated. "
            "Check that %r is available on this host.",
            DISPLAY_QUERY_CMD,
        )

    brightness = read_brightness_pct()
    if brightness is not None:
        client.publish(TOPIC_BRIGHTNESS_STATE, payload=json.dumps(brightness), qos=1, retain=True)
        log.info("Published initial brightness: %d%%", brightness)
    else:
        log.warning("Could not read initial brightness — retained topic not updated")

    # Give retained messages for the three hardware-less settings a short
    # window to arrive before concluding none exist and publishing defaults.
    threading.Timer(RETAINED_PROBE_WINDOW_S, _publish_missing_defaults, args=(client,)).start()


def _apply_power_on(client: mqtt.Client, origin: str) -> None:
    """
    Turn the display on and record this session's provenance. Shared by the
    local touch-wake path (origin="local", called directly — see _on_wake)
    and remote power/set=on commands (origin="remote", via
    _handle_power_set). auto_sleep_thread only auto-sleeps "local" sessions
    — see its docstring and _display_on_origin.
    """
    global _display_off, _display_on_origin, _last_touch_monotonic
    if set_display("on"):
        _display_off = False
        _display_on_origin = origin
        _last_touch_monotonic = time.monotonic()
        client.publish(TOPIC_STATE, payload="on", qos=1, retain=True)
        log.info("Display set to on (origin=%s) — published state", origin)
    else:
        log.error("Failed to set display to on — state topic not updated")


def _handle_power_set(client: mqtt.Client, payload: str) -> None:
    global _display_off
    if payload not in VALID_PAYLOADS:
        log.warning("Invalid payload %r on %s — ignoring", payload, TOPIC_SET)
        return
    if payload == "off":
        is_local = (time.monotonic() - _last_touch_monotonic) <= LOCAL_COMMAND_GRACE_S
        if not is_local and (not _got_remote_sleep_retained or not _remote_sleep_allowed):
            log.warning("Remote sleep rejected — remote-sleep-allowed gate is disabled or not yet confirmed")
            return
    log.info("Received power command: %s", payload)
    if payload == "on":
        _apply_power_on(client, origin="remote")
        return
    if set_display("off"):
        _display_off = True
        client.publish(TOPIC_STATE, payload="off", qos=1, retain=True)
        log.info("Display set to off — published state")
    else:
        log.error("Failed to set display to off — state topic not updated")


def _handle_brightness_set(client: mqtt.Client, raw: bytes) -> None:
    pct = _parse_int_payload(raw)
    if pct is None or not (0 <= pct <= 100):
        log.warning("Invalid brightness payload %r on %s — ignoring", raw, TOPIC_BRIGHTNESS_SET)
        return
    log.info("Received brightness command: %d%%", pct)
    if set_brightness_pct(pct):
        actual = read_brightness_pct()
        published = actual if actual is not None else pct
        client.publish(TOPIC_BRIGHTNESS_STATE, payload=json.dumps(published), qos=1, retain=True)
        log.info("Brightness set to %d%% — published state", published)
    else:
        log.error("Failed to set brightness to %d%% — state topic not updated", pct)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    global _remote_sleep_allowed, _auto_sleep_enabled, _auto_sleep_timeout_minutes
    global _got_remote_sleep_retained, _got_auto_sleep_enabled_retained, _got_auto_sleep_timeout_retained

    topic = msg.topic

    if topic == TOPIC_SET:
        try:
            payload = msg.payload.decode().strip().lower()
        except Exception:
            log.warning("Received undecodable payload on %s — ignoring", topic)
            return
        _handle_power_set(client, payload)
        return

    if topic == TOPIC_BRIGHTNESS_SET:
        _handle_brightness_set(client, msg.payload)
        return

    if topic == TOPIC_REMOTE_SLEEP_ALLOWED_STATE:
        # Our own retained state, echoed back — just confirms one exists.
        _got_remote_sleep_retained = True
        value = _parse_bool_payload(msg.payload)
        if value is not None:
            _remote_sleep_allowed = value
        return

    if topic == TOPIC_REMOTE_SLEEP_ALLOWED_SET:
        value = _parse_bool_payload(msg.payload)
        if value is None:
            log.warning("Invalid remote-sleep-allowed payload %r — ignoring", msg.payload)
            return
        _remote_sleep_allowed = value
        _got_remote_sleep_retained = True
        client.publish(TOPIC_REMOTE_SLEEP_ALLOWED_STATE, payload=json.dumps(value), qos=1, retain=True)
        log.info("remote-sleep-allowed set to %s", value)
        return

    if topic == TOPIC_AUTO_SLEEP_ENABLED_STATE:
        _got_auto_sleep_enabled_retained = True
        value = _parse_bool_payload(msg.payload)
        if value is not None:
            _auto_sleep_enabled = value
        return

    if topic == TOPIC_AUTO_SLEEP_ENABLED_SET:
        value = _parse_bool_payload(msg.payload)
        if value is None:
            log.warning("Invalid auto-sleep-enabled payload %r — ignoring", msg.payload)
            return
        _auto_sleep_enabled = value
        _got_auto_sleep_enabled_retained = True
        client.publish(TOPIC_AUTO_SLEEP_ENABLED_STATE, payload=json.dumps(value), qos=1, retain=True)
        log.info("auto-sleep-enabled set to %s", value)
        return

    if topic == TOPIC_AUTO_SLEEP_TIMEOUT_STATE:
        _got_auto_sleep_timeout_retained = True
        value = _parse_int_payload(msg.payload)
        if value is not None:
            _auto_sleep_timeout_minutes = value
        return

    if topic == TOPIC_AUTO_SLEEP_TIMEOUT_SET:
        value = _parse_int_payload(msg.payload)
        if value not in AUTO_SLEEP_TIMEOUT_CHOICES:
            log.warning("Invalid auto-sleep-timeout-minutes payload %r — ignoring", msg.payload)
            return
        _auto_sleep_timeout_minutes = value
        _got_auto_sleep_timeout_retained = True
        client.publish(TOPIC_AUTO_SLEEP_TIMEOUT_STATE, payload=json.dumps(value), qos=1, retain=True)
        log.info("auto-sleep-timeout-minutes set to %d", value)
        return

    log.warning("Received message on unexpected topic %s — ignoring", topic)


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
        """Called from the touch thread when a touch wakes a sleeping
        display. Applies the wake directly (origin="local") rather than
        round-tripping through TOPIC_SET — avoids racing the loopback
        against _on_touch's origin flip and correctly marks this as a
        host-local session for auto_sleep_thread."""
        log.info("Touch-to-wake: waking display")
        _apply_power_on(client, origin="local")

    def _on_touch():
        """Called from the touch thread on every touch; resets the idle
        clock and confirms host-local presence (flips origin back to
        "local" even if the current session was woken remotely)."""
        global _last_touch_monotonic, _display_on_origin
        _last_touch_monotonic = time.monotonic()
        _display_on_origin = "local"

    def _trigger_auto_sleep():
        """Called from the auto-sleep thread; sleeps the display directly,
        bypassing the remote-sleep-allowed gate (host-local decision)."""
        global _display_off
        if set_display("off"):
            _display_off = True
            client.publish(TOPIC_STATE, payload="off", qos=1, retain=True)
        else:
            log.error("Auto-sleep: failed to set display off")

    def _auto_sleep_state():
        return _auto_sleep_enabled, _auto_sleep_timeout_minutes, _last_touch_monotonic, _display_on_origin

    touch_thread = threading.Thread(
        target=touch_wake_thread,
        args=(lambda: _display_off, _on_wake, _on_touch),
        daemon=True,
        name="touch-wake",
    )
    touch_thread.start()

    idle_thread = threading.Thread(
        target=auto_sleep_thread,
        args=(_auto_sleep_state, lambda: _display_off, _trigger_auto_sleep),
        daemon=True,
        name="auto-sleep",
    )
    idle_thread.start()

    client.loop_forever()


if __name__ == "__main__":
    main()
