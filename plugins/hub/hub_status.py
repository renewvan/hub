#!/usr/bin/env python3
"""
renewvan hub status plugin.

Publishes the Pi's own local-network address retained to renewvan/hub/status,
so the dashboard can show the hub's IP without a router Device being installed.

Topic:   renewvan/hub/status  (retained)
Payload: {"ip": str|null, "hostname": str|null, "version": str}   (schema/hub-status.schema.json)

`ip` is the source address of the Pi's default route: the address it uses to
reach the LAN/internet. That excludes the Docker bridges and the Tailscale
address, which are not where a phone or laptop on the van's network reaches it.

`version` is the hub release the Pi was deployed from: bin/deploy.sh writes the
deploying checkout's `git describe --tags --always --dirty` to a VERSION file
beside this script. With no stamp the literal "dev" is published, never empty.

Configuration via environment (or /etc/renewvan/hub.env):
  MQTT_HOST        default: localhost
  MQTT_PORT        default: 1883
  MQTT_USERNAME    default: (empty)
  MQTT_PASSWORD    default: (empty)
  POLL_INTERVAL    default: 30  (seconds)
"""

import json
import logging
import os
import socket
import time
from pathlib import Path

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

TOPIC_STATUS = "renewvan/hub/status"

# Written by bin/deploy.sh after it syncs this directory; absent on a checkout.
VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def hub_version() -> str:
    """The stamped hub version, or "dev" when no stamp exists or it is blank."""
    try:
        return VERSION_FILE.read_text().strip() or "dev"
    except OSError:
        return "dev"


# A routable address that is never contacted: connecting a UDP socket only asks
# the kernel which local address it would send from, it transmits nothing.
_ROUTE_PROBE = ("192.0.2.1", 9)  # TEST-NET-1 (RFC 5737)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hub] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def default_route_ip() -> str | None:
    """Source IPv4 of the default route, or None when the Pi has no route."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(_ROUTE_PROBE)
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def hub_status() -> dict:
    """The payload for renewvan/hub/status."""
    return {
        "ip": default_route_ip(),
        "hostname": socket.gethostname() or None,
        "version": hub_version(),
    }


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc != 0:
        log.error("MQTT connect failed (rc=%s) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)


def on_disconnect(client: mqtt.Client, userdata, flags, rc=None, properties=None):
    if rc not in (None, 0) and getattr(rc, "value", rc) != 0:
        log.warning("Unexpected MQTT disconnect (rc=%s) — paho will reconnect", rc)


def main():
    log.info("hub plugin starting (broker=%s:%d, poll=%ds)", MQTT_HOST, MQTT_PORT, POLL_INTERVAL)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as exc:
            log.warning("Cannot reach broker (%s) — retrying in 60s", exc)
            time.sleep(60)

    client.loop_start()

    last: dict | None = None
    try:
        while True:
            status = hub_status()
            # Retained, so a late subscriber still gets it; republish every poll
            # anyway so a broker restart that dropped retained state heals.
            client.publish(TOPIC_STATUS, json.dumps(status), qos=1, retain=True)
            if status != last:
                log.info(
                    "Published status: ip=%s hostname=%s version=%s",
                    status["ip"],
                    status["hostname"],
                    status["version"],
                )
                last = status
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
