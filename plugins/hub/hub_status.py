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
"""

import json
import logging
import os
import re
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
ICONS_PATH = Path(os.environ.get("ICONS_PATH", "/var/lib/renewvan-hub/icons.json"))
RELAY_GROUPS_PATH = Path(
    os.environ.get("RELAY_GROUPS_PATH", "/var/lib/renewvan-hub/relay-groups.json")
)
RELAY_GROUP_LIST_PATH = Path(
    os.environ.get("RELAY_GROUP_LIST_PATH", "/var/lib/renewvan-hub/relay-group-list.json")
)
RELAY_ORDER_PATH = Path(
    os.environ.get("RELAY_ORDER_PATH", "/var/lib/renewvan-hub/relay-order.json")
)

TOPIC_STATUS = "renewvan/hub/status"
TOPIC_PRIMARY_BATTERY = "renewvan/hub/primary-battery"
TOPIC_PRIMARY_BATTERY_SET = "renewvan/hub/primary-battery/set"
TOPIC_ICONS = "renewvan/hub/icons"
TOPIC_ICONS_SET = "renewvan/hub/icons/set"
TOPIC_RELAY_GROUPS = "renewvan/hub/relay-groups"
TOPIC_RELAY_GROUPS_SET = "renewvan/hub/relay-groups/set"
TOPIC_RELAY_GROUP_LIST = "renewvan/hub/relay-group-list"
TOPIC_RELAY_GROUP_LIST_SET = "renewvan/hub/relay-group-list/set"
TOPIC_RELAY_ORDER = "renewvan/hub/relay-order"
TOPIC_RELAY_ORDER_SET = "renewvan/hub/relay-order/set"

# '<entity>/<id>' and a lucide component name: the same patterns as
# schema/hub-icons.schema.json.
ICON_KEY_RE = re.compile(r"^[a-z][a-z0-9]*/[^/#+\s]+$")
ICON_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,63}$")

# A relay id and a group name: the same rules as schema/hub-relay-groups.schema.json. A group
# name is 1-32 characters with no leading or trailing whitespace (fullmatch: `$` would accept a
# trailing newline).
RELAY_ID_RE = re.compile(r"[^/#+\s]+")
RELAY_GROUP_RE = re.compile(r"\S(.*\S)?")
RELAY_GROUP_MAX = 32
RELAY_GROUP_LIST_MAX = 16
RELAY_ORDER_MAX = 512
DEFAULT_RELAY_GROUP_LIST = ["Lights", "Appliances", "USB", "Water", "Heating"]

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


def _write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` atomically: a crash mid-write never leaves a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_primary_battery(value: str) -> None:
    _write_atomic(PRIMARY_BATTERY_PATH, json.dumps(value))


def publish_primary_battery(client: mqtt.Client, value: str) -> None:
    client.publish(TOPIC_PRIMARY_BATTERY, json.dumps(value), qos=1, retain=True)


def parse_icon_command(raw: bytes) -> tuple[str, str | None] | None:
    """The (key, icon) of an icons command, None when invalid. icon None clears the entry.

    Valid is the contract of schema/hub-icon-set.schema.json: exactly `key` and `icon`,
    a '<entity>/<id>' key and a lucide component name or null.
    """
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"key", "icon"}:
        return None
    key, icon = value["key"], value["icon"]
    if not isinstance(key, str) or not ICON_KEY_RE.match(key):
        return None
    if icon is not None and (not isinstance(icon, str) or not ICON_NAME_RE.match(icon)):
        return None
    return key, icon


def load_icons() -> dict[str, str]:
    """The persisted icons map, or {} when nothing valid is stored.

    An entry of the wrong shape is dropped on its own: one bad entry never costs the rest.
    """
    try:
        raw = ICONS_PATH.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.error("Cannot read %s: %s — using no icons", ICONS_PATH, exc)
        return {}
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict):
        log.warning("Ignoring invalid %s — using no icons", ICONS_PATH)
        return {}
    return {
        key: icon
        for key, icon in value.items()
        if ICON_KEY_RE.match(key) and isinstance(icon, str) and ICON_NAME_RE.match(icon)
    }


def save_icons(icons: dict[str, str]) -> None:
    _write_atomic(ICONS_PATH, json.dumps(icons))


def publish_icons(client: mqtt.Client, icons: dict[str, str]) -> None:
    client.publish(TOPIC_ICONS, json.dumps(icons), qos=1, retain=True)


def valid_relay_group(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= RELAY_GROUP_MAX
        and RELAY_GROUP_RE.fullmatch(value) is not None
    )


def valid_relay_group_list(value: object) -> bool:
    """A JSON array of at most RELAY_GROUP_LIST_MAX valid, distinct names (it may be empty)."""
    return (
        isinstance(value, list)
        and len(value) <= RELAY_GROUP_LIST_MAX
        and all(valid_relay_group(name) for name in value)
        and len(set(value)) == len(value)
    )


def parse_relay_group_list(raw: bytes) -> list[str] | None:
    """The names in a relay-group-list payload, None when invalid."""
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    return value if valid_relay_group_list(value) else None


def load_relay_group_list() -> list[str]:
    """The persisted group list, or the stock list when nothing valid is stored."""
    try:
        raw = RELAY_GROUP_LIST_PATH.read_bytes()
    except FileNotFoundError:
        return list(DEFAULT_RELAY_GROUP_LIST)
    except OSError as exc:
        log.error("Cannot read %s: %s — using the stock groups", RELAY_GROUP_LIST_PATH, exc)
        return list(DEFAULT_RELAY_GROUP_LIST)
    names = parse_relay_group_list(raw)
    if names is None:
        log.warning("Ignoring invalid %s — using the stock groups", RELAY_GROUP_LIST_PATH)
        return list(DEFAULT_RELAY_GROUP_LIST)
    return names


def save_relay_group_list(names: list[str]) -> None:
    _write_atomic(RELAY_GROUP_LIST_PATH, json.dumps(names))


def publish_relay_group_list(client: mqtt.Client, names: list[str]) -> None:
    client.publish(TOPIC_RELAY_GROUP_LIST, json.dumps(names), qos=1, retain=True)


def parse_relay_order(raw: bytes) -> list[str] | None:
    """The relay ids in a relay-order payload, None when invalid (schema/hub-relay-order)."""
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        not isinstance(value, list)
        or len(value) > RELAY_ORDER_MAX
        or not all(isinstance(i, str) and RELAY_ID_RE.fullmatch(i) for i in value)
        or len(set(value)) != len(value)
    ):
        return None
    return value


def load_relay_order() -> list[str]:
    """The persisted relay order, or [] when nothing valid is stored."""
    try:
        raw = RELAY_ORDER_PATH.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        log.error("Cannot read %s: %s — using no relay order", RELAY_ORDER_PATH, exc)
        return []
    order = parse_relay_order(raw)
    if order is None:
        log.warning("Ignoring invalid %s — using no relay order", RELAY_ORDER_PATH)
        return []
    return order


def save_relay_order(order: list[str]) -> None:
    _write_atomic(RELAY_ORDER_PATH, json.dumps(order))


def publish_relay_order(client: mqtt.Client, order: list[str]) -> None:
    client.publish(TOPIC_RELAY_ORDER, json.dumps(order), qos=1, retain=True)


def parse_relay_group_command(raw: bytes) -> tuple[str, str | None] | None:
    """The (id, group) of a relay-groups command, None when invalid. group None clears the entry.

    Valid is the contract of schema/hub-relay-group-set.schema.json: exactly `id` and `group`,
    a relay id and a group name or null.
    """
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"id", "group"}:
        return None
    relay_id, group = value["id"], value["group"]
    if not isinstance(relay_id, str) or RELAY_ID_RE.fullmatch(relay_id) is None:
        return None
    if group is not None and not valid_relay_group(group):
        return None
    return relay_id, group


def load_relay_groups() -> dict[str, str]:
    """The persisted relay groups map, or {} when nothing valid is stored.

    An entry of the wrong shape is dropped on its own: one bad entry never costs the rest.
    """
    try:
        raw = RELAY_GROUPS_PATH.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.error("Cannot read %s: %s — using no relay groups", RELAY_GROUPS_PATH, exc)
        return {}
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict):
        log.warning("Ignoring invalid %s — using no relay groups", RELAY_GROUPS_PATH)
        return {}
    return {
        relay_id: group
        for relay_id, group in value.items()
        if RELAY_ID_RE.fullmatch(relay_id) is not None and valid_relay_group(group)
    }


def save_relay_groups(groups: dict[str, str]) -> None:
    _write_atomic(RELAY_GROUPS_PATH, json.dumps(groups))


def publish_relay_groups(client: mqtt.Client, groups: dict[str, str]) -> None:
    client.publish(TOPIC_RELAY_GROUPS, json.dumps(groups), qos=1, retain=True)


def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc != 0:
        log.error("MQTT connect failed (rc=%s) — will retry", rc)
        return
    log.info("Connected to MQTT broker %s:%d", MQTT_HOST, MQTT_PORT)

    client.subscribe(TOPIC_PRIMARY_BATTERY_SET, qos=1)
    value = load_primary_battery()
    publish_primary_battery(client, value)
    log.info("Published primary battery: %s", value)

    client.subscribe(TOPIC_ICONS_SET, qos=1)
    icons = load_icons()
    publish_icons(client, icons)
    log.info("Published %d entity icon(s)", len(icons))

    client.subscribe(TOPIC_RELAY_GROUPS_SET, qos=1)
    groups = load_relay_groups()
    publish_relay_groups(client, groups)
    log.info("Published %d relay group assignment(s)", len(groups))

    client.subscribe(TOPIC_RELAY_GROUP_LIST_SET, qos=1)
    names = load_relay_group_list()
    publish_relay_group_list(client, names)
    log.info("Published %d relay group(s)", len(names))

    client.subscribe(TOPIC_RELAY_ORDER_SET, qos=1)
    order = load_relay_order()
    publish_relay_order(client, order)
    log.info("Published a relay order of %d relay(s)", len(order))


def on_icons_message(client: mqtt.Client, msg) -> None:
    command = parse_icon_command(msg.payload)
    if command is None:
        log.warning(
            'Ignoring invalid icons command %r (want {"key": "<entity>/<id>", "icon": name|null})',
            msg.payload,
        )
        return
    key, icon = command
    icons = load_icons()
    if icon is None:
        icons.pop(key, None)
    else:
        icons[key] = icon
    try:
        save_icons(icons)
    except OSError as exc:
        # Publishing a map that would silently revert on restart is worse than
        # visibly not applying it.
        log.error("Cannot persist icons to %s: %s — not applied", ICONS_PATH, exc)
        return
    publish_icons(client, icons)
    log.info("Icon for %s set to %s", key, icon)


def on_relay_groups_message(client: mqtt.Client, msg) -> None:
    command = parse_relay_group_command(msg.payload)
    if command is None:
        log.warning(
            "Ignoring invalid relay-groups command %r "
            '(want {"id": "<relay id>", "group": name|null})',
            msg.payload,
        )
        return
    relay_id, group = command
    groups = load_relay_groups()
    if group is None:
        groups.pop(relay_id, None)
    else:
        groups[relay_id] = group
    try:
        save_relay_groups(groups)
    except OSError as exc:
        # Publishing a map that would silently revert on restart is worse than
        # visibly not applying it.
        log.error("Cannot persist relay groups to %s: %s — not applied", RELAY_GROUPS_PATH, exc)
        return
    publish_relay_groups(client, groups)
    log.info("Relay %s group set to %s", relay_id, group)


def on_relay_group_list_message(client: mqtt.Client, msg) -> None:
    names = parse_relay_group_list(msg.payload)
    if names is None:
        log.warning(
            "Ignoring invalid relay-group-list command %r "
            "(want a JSON array of up to %d distinct names)",
            msg.payload,
            RELAY_GROUP_LIST_MAX,
        )
        return
    try:
        save_relay_group_list(names)
    except OSError as exc:
        # Publishing a list that would silently revert on restart is worse than
        # visibly not applying it.
        log.error("Cannot persist relay groups to %s: %s — not applied", RELAY_GROUP_LIST_PATH, exc)
        return
    publish_relay_group_list(client, names)
    log.info("Relay group list set to %s", names)


def on_relay_order_message(client: mqtt.Client, msg) -> None:
    order = parse_relay_order(msg.payload)
    if order is None:
        log.warning(
            "Ignoring invalid relay-order command %r "
            "(want a JSON array of up to %d distinct relay ids)",
            msg.payload,
            RELAY_ORDER_MAX,
        )
        return
    try:
        save_relay_order(order)
    except OSError as exc:
        # Publishing an order that would silently revert on restart is worse than
        # visibly not applying it.
        log.error("Cannot persist relay order to %s: %s — not applied", RELAY_ORDER_PATH, exc)
        return
    publish_relay_order(client, order)
    log.info("Relay order set (%d relay(s))", len(order))


def on_message(client: mqtt.Client, userdata, msg):
    if msg.topic == TOPIC_RELAY_ORDER_SET:
        on_relay_order_message(client, msg)
        return
    if msg.topic == TOPIC_RELAY_GROUP_LIST_SET:
        on_relay_group_list_message(client, msg)
        return
    if msg.topic == TOPIC_ICONS_SET:
        on_icons_message(client, msg)
        return
    if msg.topic == TOPIC_RELAY_GROUPS_SET:
        on_relay_groups_message(client, msg)
        return
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
