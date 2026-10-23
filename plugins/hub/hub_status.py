#!/usr/bin/env python3
"""
renewvan hub status plugin.

Publishes the Pi's own local-network address retained to renewvan/hub/status,
so the dashboard can show the hub's IP without a router Driver being installed.

Topic:   renewvan/hub/status  (retained)
Payload: {"ip": str|null, "hostname": str|null, "version": str}   (schema/hub-status.schema.json)

`ip` is the source address of the Pi's default route: the address it uses to
reach the LAN/internet. That excludes the Docker bridges and the Tailscale
address, which are not where a phone or laptop on the van's network reaches it.

`version` is the hub release the Pi was deployed from: bin/deploy.sh writes the
deploying checkout's `git describe --tags --always --dirty` to a VERSION file
beside this script. With no stamp the literal "dev" is published, never empty.

Primary battery: the one battery instance every client treats as the headline
battery. This plugin owns the setting (it is shared, so no client may keep its own):

  renewvan/hub/primary-battery       (retained)      state, JSON string: "auto" or an instance id
  renewvan/hub/primary-battery/set   (not retained)  command, same payload
                                     (schema/hub-primary-battery.schema.json)

A client publishes the command; this plugin persists it to PRIMARY_BATTERY_PATH
and republishes the retained state. On every (re)connect it publishes the
persisted value (default "auto"). It does not know which instances exist, so it
only rejects payloads that are not a non-empty JSON string (ignored and logged).

Configuration via environment (or /etc/renewvan/hub.env):
  MQTT_HOST              default: localhost
  MQTT_PORT              default: 1883
  MQTT_USERNAME          default: (empty)
  MQTT_PASSWORD          default: (empty)
  POLL_INTERVAL          default: 30  (seconds)
  PRIMARY_BATTERY_PATH   default: /var/lib/renewvan-hub/primary-battery.json
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
# renewvan-hub.service declares StateDirectory=renewvan-hub, so systemd creates
# /var/lib/renewvan-hub owned by the service user.
PRIMARY_BATTERY_PATH = Path(
    os.environ.get("PRIMARY_BATTERY_PATH", "/var/lib/renewvan-hub/primary-battery.json")
)

TOPIC_STATUS = "renewvan/hub/status"
TOPIC_PRIMARY_BATTERY = "renewvan/hub/primary-battery"
TOPIC_PRIMARY_BATTERY_SET = "renewvan/hub/primary-battery/set"

DEFAULT_PRIMARY_BATTERY = "auto"

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


def parse_primary_battery(raw: bytes) -> str | None:
    """The instance id (or "auto") in a primary-battery payload, None when invalid.

    Valid is a JSON string that is not empty: the same contract as
    schema/hub-primary-battery.schema.json. Whether the id names a live
    instance is the consumer's call, not this plugin's.
    """
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, str) or not value:
        return None
    return value


def load_primary_battery() -> str:
    """The persisted primary battery, or "auto" when nothing valid is stored."""
    try:
        raw = PRIMARY_BATTERY_PATH.read_bytes()
    except FileNotFoundError:
        return DEFAULT_PRIMARY_BATTERY
    except OSError as exc:
        log.error(
            "Cannot read %s: %s — using %r", PRIMARY_BATTERY_PATH, exc, DEFAULT_PRIMARY_BATTERY
        )
        return DEFAULT_PRIMARY_BATTERY
    value = parse_primary_battery(raw)
    if value is None:
        log.warning("Ignoring invalid %s — using %r", PRIMARY_BATTERY_PATH, DEFAULT_PRIMARY_BATTERY)
        return DEFAULT_PRIMARY_BATTERY
    return value


def save_primary_battery(value: str) -> None:
    """Persist `value` atomically: a crash mid-write never leaves a torn file."""
    PRIMARY_BATTERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PRIMARY_BATTERY_PATH.with_name(PRIMARY_BATTERY_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(value))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PRIMARY_BATTERY_PATH)


def publish_primary_battery(client: mqtt.Client, value: str) -> None:
    client.publish(TOPIC_PRIMARY_BATTERY, json.dumps(value), qos=1, retain=True)


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc != 0:
        log.error("MQTT connect failed (rc=%s) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)

    client.subscribe(TOPIC_PRIMARY_BATTERY_SET, qos=1)
    value = load_primary_battery()
    publish_primary_battery(client, value)
    log.info("Published primary battery: %s", value)


def on_message(client: mqtt.Client, userdata, msg):
    if msg.topic != TOPIC_PRIMARY_BATTERY_SET:
        return
    value = parse_primary_battery(msg.payload)
    if value is None:
        log.warning(
            "Ignoring invalid primary-battery command %r (want a non-empty JSON string)",
            msg.payload,
        )
        return
    try:
        save_primary_battery(value)
    except OSError as exc:
        # Publishing a value that would silently revert on restart is worse than
        # visibly not applying it.
        log.error(
            "Cannot persist primary battery %r to %s: %s — not applied",
            value,
            PRIMARY_BATTERY_PATH,
            exc,
        )
        return
    publish_primary_battery(client, value)
    log.info("Primary battery set to %s", value)


def on_disconnect(client: mqtt.Client, userdata, flags, rc=None, properties=None):
    if rc not in (None, 0) and getattr(rc, "value", rc) != 0:
        log.warning("Unexpected MQTT disconnect (rc=%s) — paho will reconnect", rc)


def main():
    log.info("hub plugin starting (broker=%s:%d, poll=%ds)", MQTT_HOST, MQTT_PORT, POLL_INTERVAL)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
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
