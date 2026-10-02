#!/usr/bin/env python3
"""
renewvan Tailscale status plugin.

Polls `tailscale status --json` every POLL_INTERVAL seconds and publishes
the result retained to renewvan/tailscale/status.

Topic:   renewvan/tailscale/status  (retained)
Payload: {"enabled": bool, "connected": bool, "ip": str|null,
          "hostname": str|null, "peers": int}

Configuration via environment (or /etc/renewvan/tailscale.env):
  MQTT_HOST        default: localhost
  MQTT_PORT        default: 1883
  MQTT_USERNAME    default: (empty)
  MQTT_PASSWORD    default: (empty)
  POLL_INTERVAL    default: 30  (seconds)
"""

import json
import logging
import os
import subprocess
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

TOPIC_STATUS = "renewvan/tailscale/status"

DISABLED_PAYLOAD = {
    "enabled": False,
    "connected": False,
    "ip": None,
    "hostname": None,
    "peers": 0,
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tailscale] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tailscale helpers
# ---------------------------------------------------------------------------


def query_tailscale() -> dict:
    """
    Run `tailscale status --json` and return a normalised status dict.
    Returns DISABLED_PAYLOAD on any error (not installed, daemon down, etc.).
    """
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        log.debug("tailscale binary not found")
        return DISABLED_PAYLOAD
    except subprocess.TimeoutExpired:
        log.warning("tailscale status timed out")
        return DISABLED_PAYLOAD
    except Exception as exc:
        log.warning("tailscale status error: %s", exc)
        return DISABLED_PAYLOAD

    if result.returncode != 0:
        log.debug("tailscale status exited %d: %s", result.returncode, result.stderr.strip())
        return DISABLED_PAYLOAD

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        log.warning("tailscale status JSON parse error: %s", exc)
        return DISABLED_PAYLOAD

    backend = data.get("BackendState", "")
    connected = backend == "Running"
    enabled = backend not in ("", "NoState", "NeedsLogin", "Stopped")

    ip = None
    hostname = None
    if connected:
        ips = data.get("TailscaleIPs", [])
        # prefer IPv4 (100.x.x.x)
        for addr in ips:
            if addr.startswith("100."):
                ip = addr
                break
        if ip is None and ips:
            ip = ips[0]
        self_info = data.get("Self", {})
        hostname = self_info.get("HostName") or self_info.get("DNSName", "").split(".")[0] or None

    peers = 0
    if connected:
        peers_map = data.get("Peer", {}) or {}
        peers = sum(1 for p in peers_map.values() if p.get("Online", False))

    return {
        "enabled": enabled or connected,
        "connected": connected,
        "ip": ip,
        "hostname": hostname,
        "peers": peers,
    }


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

_client: mqtt.Client | None = None


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc != 0:
        log.error("MQTT connect failed (rc=%d) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)


def on_disconnect(client: mqtt.Client, userdata, rc, properties=None):
    if rc != 0:
        log.warning("Unexpected MQTT disconnect (rc=%d) — paho will reconnect", rc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    log.info(
        "tailscale plugin starting (broker=%s:%d, poll=%ds)", MQTT_HOST, MQTT_PORT, POLL_INTERVAL
    )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # Connect with retry
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as exc:
            log.warning("Cannot reach broker (%s) — retrying in 60s", exc)
            time.sleep(60)

    client.loop_start()

    try:
        while True:
            status = query_tailscale()
            payload = json.dumps(status)
            client.publish(TOPIC_STATUS, payload, qos=1, retain=True)
            log.info(
                "Published status: connected=%s ip=%s peers=%d",
                status["connected"],
                status["ip"],
                status["peers"],
            )
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
