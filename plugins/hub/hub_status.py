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

Entity icons: which icon every client draws an entity instance with. A presentation
choice, so it lives here and never on the entity's own topics (ADR 0023). Shared, so
no client may keep its own:

  renewvan/hub/icons       (retained)      state, JSON object: {"<entity>/<id>": "<LucideName>"}
  renewvan/hub/icons/set   (not retained)  command, {"key": "<entity>/<id>", "icon": name|null}
                           (schema/hub-icons.schema.json, schema/hub-icon-set.schema.json)

A client publishes the command (icon null clears the entry); this plugin persists the
whole map to ICONS_PATH and republishes it retained. On every (re)connect it publishes
the persisted map (default {}). It does not know which instances exist or which icons
a consumer can draw, so it only rejects a command or a stored entry of the wrong shape.

Relay groups: which group (Lights, Appliances, USB, ...) every client files a relay channel
under. Presentation, so it lives here and never on the relay's own topics, and shared, so
no client may keep its own (ADR 0028):

  renewvan/hub/relay-groups       (retained)      state, JSON object: {"<relay id>": "<group name>"}
  renewvan/hub/relay-groups/set   (not retained)  command, {"id": "<relay id>", "group": name|null}
                                  (schema/hub-relay-groups.schema.json,
                                   schema/hub-relay-group-set.schema.json)

A client publishes the command (group null clears the entry, back to Unassigned); this plugin
persists the whole map to RELAY_GROUPS_PATH and republishes it retained. On every (re)connect
it publishes the persisted map (default {}). It does not know which relays exist, so it only
rejects a command or a stored entry of the wrong shape.

Relay group list: the groups a client offers for a relay and the order it lists them in. Also
shared, so the owner edits it once for every client (ADR 0028):

  renewvan/hub/relay-group-list       (retained)      state, JSON array of group names
  renewvan/hub/relay-group-list/set   (not retained)  command, same payload
                                      (schema/hub-relay-group-list.schema.json)

Default is the five stock groups, until the first valid command. The name "Unassigned" stands
for the relays with no group: its place in the list is where a client draws them. The plugin does
not touch the assignments when a name leaves the list: a consumer reads an assignment to a name
that is not in the list as Unassigned, and the client that removes a group clears its relays first.

Relay order: the order a client lists relay channels in inside a group card, as one array of
relay ids (ADR 0028):

  renewvan/hub/relay-order       (retained)      state, JSON array of relay ids, first to last
  renewvan/hub/relay-order/set   (not retained)  command, same payload
                                 (schema/hub-relay-order.schema.json)

Default []. A relay missing from the array sorts after the listed ones. The plugin does not know
which relays exist, so it only checks the shape of the array.

Winter mode: Start winter mode shuts the hub down cleanly once the Hub power board has confirmed it
will cut the power (ADR 0033). See winter_mode.py for its topics, refusals and order.

Fleet: which Drivers the van has, and the bus contract version, as one retained topic maintained
from the Drivers' info topics, so a consumer can settle after the retained replay instead of
guessing (ADR 0035). See fleet.py:

  renewvan/hub/fleet  (retained)  {"contract": 1, "drivers": ["tank", "venus", ...]}
                                  (schema/hub-fleet.schema.json)

Configuration via environment (or /etc/renewvan/hub.env):
  MQTT_HOST              default: localhost
  MQTT_PORT              default: 1883
  MQTT_USERNAME          default: (empty)
  MQTT_PASSWORD          default: (empty)
  POLL_INTERVAL          default: 30  (seconds)
  PRIMARY_BATTERY_PATH   default: /var/lib/renewvan-hub/primary-battery.json
  ICONS_PATH             default: /var/lib/renewvan-hub/icons.json
  RELAY_GROUPS_PATH      default: /var/lib/renewvan-hub/relay-groups.json
  RELAY_GROUP_LIST_PATH  default: /var/lib/renewvan-hub/relay-group-list.json
  RELAY_ORDER_PATH       default: /var/lib/renewvan-hub/relay-order.json
  HUB_SCHEMA_DIR         default: schema/ beside this script (bin/deploy.sh ships the
                         hub-*.schema.json files there), else the repo's schema/

Each setting is a declaration (SETTINGS below) of shared_setting.SharedSetting, which owns the
topics, the atomic persistence, the default and the shape check compiled from the schema file.
"""

import json
import logging
import os
import socket
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from shared_setting import SharedSetting, read_schema, set_entry
from winter_mode import WinterMode
from fleet import Fleet

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

TOPIC_STATUS = "renewvan/hub/status"

DEFAULT_RELAY_GROUP_LIST = ["Lights", "Appliances", "USB", "Water", "Heating"]

# The shared settings, each a declaration: its name (renewvan/hub/<name>, .../set and the file
# <NAME>_PATH, by default /var/lib/renewvan-hub/<name>.json, created by the unit's StateDirectory=),
# the schema of its state, its default, and how a command merges into the state (none: a command
# is the whole new state). The checks are compiled from the schema files themselves.
PRIMARY_BATTERY = SharedSetting("primary-battery", read_schema("hub-primary-battery"), "auto")
ICONS = SharedSetting(
    "icons",
    read_schema("hub-icons"),
    {},
    command_schema=read_schema("hub-icon-set"),
    merge=set_entry("key", "icon"),
)
RELAY_GROUPS = SharedSetting(
    "relay-groups",
    read_schema("hub-relay-groups"),
    {},
    command_schema=read_schema("hub-relay-group-set"),
    merge=set_entry("id", "group"),
)
RELAY_GROUP_LIST = SharedSetting(
    "relay-group-list", read_schema("hub-relay-group-list"), DEFAULT_RELAY_GROUP_LIST
)
RELAY_ORDER = SharedSetting("relay-order", read_schema("hub-relay-order"), [])

SETTINGS = (PRIMARY_BATTERY, ICONS, RELAY_GROUPS, RELAY_GROUP_LIST, RELAY_ORDER)
_BY_SET_TOPIC = {setting.set_topic: setting for setting in SETTINGS}

WINTER_MODE = WinterMode()
FLEET = Fleet()

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
    for setting in SETTINGS:
        setting.connect(client)
    WINTER_MODE.connect(client)
    FLEET.connect(client)


def on_message(client: mqtt.Client, userdata, msg):
    setting = _BY_SET_TOPIC.get(msg.topic)
    if setting is not None:
        setting.command(client, msg.payload)
    elif not FLEET.message(client, msg):
        WINTER_MODE.message(client, msg)


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
