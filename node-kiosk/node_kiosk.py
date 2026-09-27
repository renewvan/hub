#!/usr/bin/env python3
"""
renewvan/node-kiosk — display power bridge.

Subscribes to  renewvan/kiosk/display/power/set  ("on" | "off").
Executes the configured display-power command (default: vcgencmd display_power).
Publishes the resulting state retained to  renewvan/kiosk/display/power.

On startup the node probes the display-power command to read the current state
and publishes it as the initial retained value, so the bus always reflects
hardware reality rather than the last command.

If the display-power command is unavailable the node logs an error, publishes
nothing, and continues — the dashboard and broker are unaffected.

Environment variables (all optional, defaults match the hub compose stack):
  MQTT_HOST          MQTT broker hostname     (default: localhost)
  MQTT_PORT          MQTT broker port         (default: 1883)
  MQTT_USERNAME      broker username          (default: empty)
  MQTT_PASSWORD      broker password          (default: empty)
  DISPLAY_ON_CMD     shell command to turn display on  (default: vcgencmd display_power 1)
  DISPLAY_OFF_CMD    shell command to turn display off (default: vcgencmd display_power 0)
  DISPLAY_QUERY_CMD  shell command to query display state; stdout is parsed for
                     "display_power=1" (on) or "display_power=0" (off)
                     (default: vcgencmd display_power)
"""

import logging
import os
import shlex
import subprocess
import sys
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")

DISPLAY_ON_CMD = os.environ.get("DISPLAY_ON_CMD", "vcgencmd display_power 1")
DISPLAY_OFF_CMD = os.environ.get("DISPLAY_OFF_CMD", "vcgencmd display_power 0")
DISPLAY_QUERY_CMD = os.environ.get("DISPLAY_QUERY_CMD", "vcgencmd display_power")

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
        log.error("Command not found: %r — is vcgencmd (or the configured DISPLAY_*_CMD) available?", cmd)
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
    Returns "on", "off", or None if the command is unavailable/unreadable.
    """
    ok, stdout = _run(DISPLAY_QUERY_CMD)
    if not ok:
        return None
    # vcgencmd display_power output: "display_power=1" or "display_power=0"
    if "display_power=1" in stdout:
        return "on"
    if "display_power=0" in stdout:
        return "off"
    # Pluggable command: treat exit-0 with any output as "on", no output as unknown.
    log.warning("Unrecognised display query output: %r — cannot determine state", stdout)
    return None


def set_display(state: str) -> bool:
    """
    Apply display power state ("on" or "off").
    Returns True on success, False on failure.
    """
    cmd = DISPLAY_ON_CMD if state == "on" else DISPLAY_OFF_CMD
    ok, _ = _run(cmd)
    return ok


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc != 0:
        log.error("MQTT connect failed (rc=%d) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)
    client.subscribe(TOPIC_SET, qos=1)
    log.info("Subscribed to %s", TOPIC_SET)

    # Publish current hardware state on every (re)connect so the retained
    # topic reflects reality after a node restart or broker reconnect.
    state = query_display_state()
    if state is not None:
        client.publish(TOPIC_STATE, payload=state, qos=1, retain=True)
        log.info("Published initial state: %s", state)
    else:
        log.warning(
            "Could not read initial display state — retained topic not updated. "
            "Check that %r is available on this host.",
            DISPLAY_QUERY_CMD,
        )


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    try:
        payload = msg.payload.decode().strip().lower()
    except Exception:
        log.warning("Received undecodable payload on %s — ignoring", msg.topic)
        return

    if payload not in VALID_PAYLOADS:
        log.warning("Received invalid payload %r on %s (expected 'on' or 'off') — ignoring", payload, msg.topic)
        return

    log.info("Received command: %s", payload)
    if set_display(payload):
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
        protocol=mqtt.MQTTv5,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # Reconnect loop: wait up to 60 s for the broker (mirrors the 60 s
    # Chromium startup wait added in the power-loss-recovery fix).
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

    client.loop_forever()


if __name__ == "__main__":
    main()
