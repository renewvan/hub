#!/usr/bin/env python3
"""Remove a Driver that was uninstalled for good, clearing the retained topics it leaves behind.

Driver info, health and entity topics are retained by design (docs/adr/0011), so an uninstalled
Driver looks exactly like an offline one and its widgets stay on screen for ever. The Driver is
gone from the bus only once its retained topics are cleared. This clears:

  renewvan/<key>/info, .../health, .../winter-mode, ...     the Driver's own namespace
  renewvan/<entity>/<id>/<anything>                          every instance that names the Driver
                                                             in its retained `driver` property

It reads what is actually retained instead of assuming a shape, so a Driver of any `publishes`
set is cleared. It refuses while the Driver's health is `online` (a live Driver would republish
at once) unless --force, warns when the Driver is the Hub power board (`hub_power` in its info,
docs/adr/0033), and verifies afterwards that nothing is left. Instances with no `driver` property
cannot be attributed (ADR 0019 keeps it optional); those of a type the removed Driver published
are listed so the operator can clear them by hand. Needs mosquitto_sub/mosquitto_pub.

Stop and uninstall the Driver first (remove it from docker-compose.yml, or power the board off),
then run this on the Pi:

usage: python3 remove_driver.py KEY [--dry-run] [--force] [--wait SECONDS] [-- MOSQUITTO ARGS...]
  e.g. python3 remove_driver.py venus --dry-run -- -h localhost -u admin -P secret

Everything after `--` is passed to mosquitto_sub and mosquitto_pub (host, port, credentials, TLS).
The credential must be allowed to publish to the Driver's topics. Importable: topics_to_clear().
"""

import argparse
import json
import re
import subprocess
import sys

BUS = "renewvan"

# The Driver key rule of schema/driver-info.schema.json, plus "hub": renewvan/hub/# is the hub
# plugin's own namespace, never a Driver's.
KEY_RE = re.compile(r"^(?!driver-)(?!demo$)(?!hub$)[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def validate_key(key):
    """A list of problems with the Driver key; empty when it is usable."""
    if not KEY_RE.match(key):
        return [
            f"'{key}' is not a Driver key: a lowercase slug with no driver- prefix, "
            "and never the reserved keys 'demo' or 'hub'"
        ]
    return []


def namespace_topics(key, topics):
    """The retained topics under the Driver's own namespace (info, health, winter-mode), sorted."""
    prefix = f"{BUS}/{key}/"
    return sorted(t for t in topics if t.startswith(prefix))


_DRIVER_TOPIC_RE = re.compile(rf"^{BUS}/([^/]+)/([^/]+)/driver$")


def attributed_instances(key, topics):
    """{(entity, id)} whose retained `driver` property is a JSON string equal to the key.

    The four-segment shape keeps the Demo namespace out: renewvan/demo/<entity>/<id>/driver has
    five segments and never matches.
    """
    found = set()
    for topic, payload in topics.items():
        match = _DRIVER_TOPIC_RE.match(topic)
        if match is None:
            continue
        try:
            if json.loads(payload) == key:
                found.add((match.group(1), match.group(2)))
        except (TypeError, ValueError):
            continue
    return found


def instance_topics(entity, instance_id, topics):
    """Every retained topic of one instance, sorted."""
    prefix = f"{BUS}/{entity}/{instance_id}/"
    return sorted(t for t in topics if t.startswith(prefix))


def topics_to_clear(key, topics):
    """The retained topics that belong to the Driver: its namespace plus its instances'."""
    owned = set(namespace_topics(key, topics))
    for entity, instance_id in attributed_instances(key, topics):
        owned.update(instance_topics(entity, instance_id, topics))
    return sorted(owned)


def _retained_info(key, topics):
    """The Driver's retained info as a dict, or {} when it has none or it is not an object."""
    try:
        payload = json.loads(topics.get(f"{BUS}/{key}/info", ""))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def publishes(key, topics):
    """The entity types the Driver's retained info declares, or [] when it has none."""
    declared = _retained_info(key, topics).get("publishes")
    return declared if isinstance(declared, list) else []


def unattributed_instances(types, topics):
    """[(entity, id)] of a published type whose instance has no retained `driver` topic.

    Such an instance cannot be attributed to any Driver (ADR 0019), so the tool reports it
    instead of guessing an owner. Instances with a `driver` topic belong to whoever it names.
    """
    attributed = {(m.group(1), m.group(2)) for t in topics if (m := _DRIVER_TOPIC_RE.match(t))}
    found = set()
    for entity in types:
        prefix = f"{BUS}/{entity}/"
        ids = {t[len(prefix) :].split("/", 1)[0] for t in topics if t.startswith(prefix)}
        found.update((entity, i) for i in ids if (entity, i) not in attributed)
    return sorted(found)


def is_online(key, topics):
    """True when the Driver's retained health says online (bare word, LWT-maintained)."""
    return topics.get(f"{BUS}/{key}/health", "").strip('" ') == "online"


def has_hub_power(key, topics):
    """True when the Driver's retained info announces the Hub power channel (ADR 0033)."""
    return bool(_retained_info(key, topics).get("hub_power"))


def discovery_filters(key):
    """The subscription filters that find everything a Driver can leave retained."""
    return [f"{BUS}/{key}/#", f"{BUS}/+/+/driver"]


def parse_retained(output):
    """`mosquitto_sub -v` lines -> {topic: payload}."""
    found = {}
    for line in output.splitlines():
        topic, _, payload = line.partition(" ")
        if topic:
            found[topic] = payload
    return found


def read_retained(filters, mosquitto_args, wait):
    """Everything retained under the filters, as {topic: payload}."""
    cmd = ["mosquitto_sub", "-v", "--retained-only", "-W", str(wait), *mosquitto_args]
    for flt in filters:
        cmd += ["-t", flt]
    # -W ends with exit code 27 when the timeout passes: that is how a quiet broker ends,
    # not a failure.
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 27):
        raise RuntimeError(f"mosquitto_sub failed ({result.returncode}): {result.stderr.strip()}")
    return parse_retained(result.stdout)


def clear(topic, mosquitto_args):
    cmd = ["mosquitto_pub", *mosquitto_args, "-r", "-n", "-t", topic]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"clearing {topic} failed: {result.stderr.strip()}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("key", help="the Driver key that was uninstalled (renewvan/<key>/...)")
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be cleared, change nothing"
    )
    parser.add_argument(
        "--force", action="store_true", help="clear even if the Driver's health is online"
    )
    parser.add_argument(
        "--wait", type=int, default=3, help="seconds to wait for retained topics (default 3)"
    )
    argv = sys.argv[1:] if argv is None else list(argv)
    # Everything after `--` belongs to mosquitto, not to this parser.
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    args.mosquitto_args = argv[split + 1 :]

    problems = validate_key(args.key)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2

    try:
        retained = read_retained(discovery_filters(args.key), args.mosquitto_args, args.wait)
        owned = topics_to_clear(args.key, retained)
        if not owned:
            print(f"nothing retained for driver '{args.key}': no ghost Driver to clear")
            return 0

        declared = publishes(args.key, retained)
        report = unattributed_instances(declared, retained)
        if args.dry_run:
            for topic in owned:
                print("would clear " + topic)
        else:
            if is_online(args.key, retained) and not args.force:
                print(
                    f"error: {args.key} is online: the Driver is still running and would "
                    "republish. Stop and uninstall it first, or pass --force.",
                    file=sys.stderr,
                )
                return 1
            if has_hub_power(args.key, retained):
                print(
                    f"warning: {args.key} is the Hub power board (hub_power in its info, "
                    "docs/adr/0033): without it the van has no Winter mode and no Hub wake.",
                    file=sys.stderr,
                )
        if not declared:
            print(
                "note: the Driver left no retained info, so its entity types are unknown and "
                "instances without a `driver` property are not reported",
                file=sys.stderr,
            )
        elif report:
            print(
                "note: not cleared, no `driver` property to attribute them: "
                + ", ".join(f"{BUS}/{e}/{i}/#" for e, i in report)
                + " — if any belonged to this Driver, clear them by hand",
                file=sys.stderr,
            )
        if args.dry_run:
            return 0

        for topic in owned:
            print("clear " + topic)
            clear(topic, args.mosquitto_args)
        left = topics_to_clear(
            args.key,
            read_retained(discovery_filters(args.key), args.mosquitto_args, args.wait),
        )
    except (RuntimeError, FileNotFoundError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    if left:
        print("error: still retained: " + ", ".join(left), file=sys.stderr)
        return 1
    print(f"cleared {len(owned)} retained topics: {args.key} no longer exists on the bus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
