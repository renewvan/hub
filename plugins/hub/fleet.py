"""The fleet: which Drivers the van has, and the bus contract version, as one retained topic.

A consumer that connects to the bus cannot tell when the retained replay ends, so it cannot tell
"no such entity type" from "not replayed yet" (apps worked around this with a fixed timer). The
fleet is the hub's answer: one retained `renewvan/hub/fleet` message listing every Driver that has
retained info, maintained by the hub plugin, so a consumer settles once it has seen the info of
every Driver the fleet names (with a timeout as the fallback). `contract` is the bus contract
version: an old consumer that sees a higher number than it was built for knows it is behind.

  renewvan/hub/fleet  (retained)  {"contract": 1, "drivers": ["tank", "venus", ...]}
                                  (schema/hub-fleet.schema.json)

The hub plugin subscribes to `renewvan/+/info` and rebuilds the set on every connect: retained
replay delivers every current info, and a Driver removed while the plugin was away (its info
cleared by bin/remove_driver.py) is simply absent. A live empty payload — the removal signal —
drops the Driver at once. The first publish waits until the replay has been collected: publishing
the just-reset set at connect time would overwrite a good retained fleet with an empty one in the
replay window, and a consumer connecting then would settle on "no Drivers installed" — the exact
false answer this topic exists to kill. The Demo namespace is out of scope by construction: demo
Driver infos sit one segment deeper (`renewvan/demo/<driver>/info`), and the reserved keys are
refused.

See docs/adr/0035-driver-removal-and-the-fleet-topic.md.
"""

import json
import logging
import re
import threading

log = logging.getLogger(__name__)

TOPIC = "renewvan/hub/fleet"
SUBSCRIPTION = "renewvan/+/info"

# The bus contract version: the entity schemas, the Driver info/health conventions and the hub
# topics, versioned as one. Bump when the contract changes in a way a consumer must know about.
CONTRACT = 1

# How long after a connect the retained replay is given before the first publish. A broker replay
# is local and fast; two seconds is generous, and a slower replay only delays the first publish.
SETTLE_S = 2.0

_INFO_RE = re.compile(r"^renewvan/([a-z][a-z0-9]*(-[a-z0-9]+)*)/info$")

# Never Drivers: renewvan/demo/# is the Demo namespace (ADR 0010) and renewvan/hub/# is the hub
# plugin's own namespace. Both match the subscription shape, so refuse them here.
_RESERVED = {"demo", "hub"}


class Fleet:
    """The set of Drivers that have retained info, republished retained on every change."""

    def __init__(self, settle_s=SETTLE_S):
        self._drivers = set()
        self._last_payload = None
        self._settle_s = settle_s
        self._settled = False
        self._timer = None

    def connect(self, client):
        """Subscribe, and rebuild from the retained replay before the first publish."""
        client.subscribe(SUBSCRIPTION, qos=1)
        self._drivers = set()
        self._settled = False
        # Republish even when the fleet is unchanged: a broker that lost retained state (the
        # replay then delivers nothing) heals only because the fleet is published again.
        self._last_payload = None
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._settle_s, self._settle, args=(client,))
        self._timer.daemon = True
        self._timer.start()

    def _settle(self, client):
        self._settled = True
        self._publish(client)

    def message(self, client, msg):
        """Track one message. Returns True when it was a Driver info message."""
        match = _INFO_RE.match(msg.topic)
        if match is None or match.group(1) in _RESERVED:
            return False
        key = match.group(1)
        if msg.payload:
            self._drivers.add(key)
        else:
            # An empty retained publish is the removal signal (bin/remove_driver.py).
            self._drivers.discard(key)
        if self._settled:
            self._publish(client)
        return True

    def _publish(self, client):
        payload = json.dumps({"contract": CONTRACT, "drivers": sorted(self._drivers)})
        if payload == self._last_payload:
            return
        client.publish(TOPIC, payload, qos=1, retain=True)
        self._last_payload = payload
        log.info("Published fleet: %s", payload)
