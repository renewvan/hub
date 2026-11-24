"""
Winter mode in the hub plugin (docs/adr/0033-winter-mode-and-hub-power.md).

  renewvan/hub/winter-mode           (retained)  state, JSON boolean
                                                 (schema/hub-winter-mode.schema.json)
  renewvan/hub/winter-mode/set       (no)        Start winter mode, `true` only, any client
  renewvan/<driver>/winter-mode/set  (no)        `true`, this plugin to the Hub power board only
  renewvan/<driver>/winter-mode      (retained)  the board's own flag
                                                 (schema/driver-winter-mode.schema.json)

The state is `false` on every connect. On Start winter mode the plugin refuses (republishing
`false`, so the client gets an answer) in Demo mode, while any heater's phase is not `off`, or
unless exactly one Driver info announces `hub_power`. Otherwise it sends that board its Winter
mode command and waits CONFIRM_TIMEOUT_S for the board's confirmation: a LIVE `true` on
renewvan/<driver>/winter-mode, never a retained one, which could be left from an earlier winter.
Confirmed, it publishes `true` and powers the Pi off POWER_OFF_GRACE_S later, so the state
reaches the broker first; the board cuts Hub power after its own delay. Not confirmed in time,
it publishes `false` and stays up: a halted Pi on a Hub power relay that is still on could not
be woken.
"""

import json
import logging
import subprocess
import threading
from typing import Callable

STATE_TOPIC = "renewvan/hub/winter-mode"
SET_TOPIC = f"{STATE_TOPIC}/set"
INFO_FILTER = "renewvan/+/info"
BOARD_STATE_FILTER = "renewvan/+/winter-mode"
BOARD_SET_FILTER = "renewvan/+/winter-mode/set"
HEATER_PHASE_FILTER = "renewvan/heater/+/phase"
DEMO_ENABLED_TOPIC = "renewvan/demo/enabled"

CONFIRM_TIMEOUT_S = 10.0
POWER_OFF_GRACE_S = 1.0

log = logging.getLogger(__name__)

Cancel = Callable[[], None]
Schedule = Callable[[float, Callable[[], None]], Cancel]


def thread_schedule(delay: float, fn: Callable[[], None]) -> Cancel:
    """Runs fn after delay seconds on a daemon timer thread; returns its cancel."""
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer.cancel


def systemctl_poweroff() -> None:
    """Powers the Pi off through logind; the polkit rule lets the plugin's service user do it."""
    result = subprocess.run(["systemctl", "poweroff"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        log.error(
            "Winter mode: power-off failed (%s): %s", result.returncode, result.stderr.strip()
        )


def _json(payload: bytes):
    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None


def _segments(topic: str) -> list[str]:
    return topic.split("/")


class WinterMode:
    def __init__(
        self,
        power_off: Callable[[], None] = systemctl_poweroff,
        schedule: Schedule = thread_schedule,
    ):
        self._power_off = power_off
        self._schedule = schedule
        self._lock = threading.Lock()
        self._hub_power: dict[str, str] = {}  # driver -> relay id, from Driver info
        self._heater_phase: dict[str, str] = {}  # heater id -> phase
        self._demo = False
        self._pending: str | None = None  # the board asked, awaiting its confirmation
        self._cancel_timeout: Cancel | None = None
        self._confirmed = False

    def connect(self, client) -> None:
        for topic in (
            SET_TOPIC,
            INFO_FILTER,
            BOARD_STATE_FILTER,
            HEATER_PHASE_FILTER,
            DEMO_ENABLED_TOPIC,
        ):
            client.subscribe(topic, qos=1)
        with self._lock:
            if not self._confirmed:
                self._publish_state(client, False)

    def message(self, client, msg) -> bool:
        """Handles msg if it is one of Winter mode's topics; returns whether it was."""
        topic = msg.topic
        parts = _segments(topic)
        with self._lock:
            if topic == SET_TOPIC:
                self._start(client, _json(msg.payload))
            elif topic == DEMO_ENABLED_TOPIC:
                self._demo = _json(msg.payload) is True
            elif len(parts) == 4 and parts[1] == "heater" and parts[3] == "phase":
                phase = _json(msg.payload)
                if isinstance(phase, str):
                    self._heater_phase[parts[2]] = phase
                else:
                    self._heater_phase.pop(parts[2], None)
            elif len(parts) == 3 and parts[2] == "info":
                self._info(parts[1], _json(msg.payload))
            elif len(parts) == 3 and parts[2] == "winter-mode":
                if parts[1] != "hub":
                    self._board_state(client, parts[1], _json(msg.payload), msg.retain)
            else:
                return False
        return True

    def _info(self, driver: str, info) -> None:
        hub_power = info.get("hub_power") if isinstance(info, dict) else None
        if isinstance(hub_power, str) and hub_power:
            self._hub_power[driver] = hub_power
        else:
            self._hub_power.pop(driver, None)

    def _refusal(self) -> str | None:
        if self._demo:
            return "Demo mode is on"
        busy = sorted(h for h, phase in self._heater_phase.items() if phase != "off")
        if busy:
            return f"heater not off: {', '.join(busy)}"
        if len(self._hub_power) != 1:
            return f"{len(self._hub_power)} Drivers announce hub_power, need exactly one"
        return None

    def _start(self, client, value) -> None:
        if value is not True or self._pending or self._confirmed:
            return
        refusal = self._refusal()
        if refusal:
            log.warning("Winter mode refused: %s", refusal)
            self._publish_state(client, False)
            return
        (driver,) = self._hub_power
        self._pending = driver
        client.publish(f"renewvan/{driver}/winter-mode/set", "true", qos=1, retain=False)
        self._cancel_timeout = self._schedule(CONFIRM_TIMEOUT_S, lambda: self._timeout(client))
        log.info("Winter mode: asked %s, waiting for its confirmation", driver)

    def _timeout(self, client) -> None:
        with self._lock:
            if not self._pending:
                return
            log.error("Winter mode aborted: %s did not confirm in time", self._pending)
            self._pending = None
            self._cancel_timeout = None
            self._publish_state(client, False)

    def _board_state(self, client, driver: str, value, retained: bool) -> None:
        if retained or value is not True or driver != self._pending:
            return
        if self._cancel_timeout:
            self._cancel_timeout()
        self._pending = None
        self._cancel_timeout = None
        self._confirmed = True
        self._publish_state(client, True)
        log.info("Winter mode: %s confirmed, powering off", driver)
        self._schedule(POWER_OFF_GRACE_S, self._power_off)

    @staticmethod
    def _publish_state(client, value: bool) -> None:
        client.publish(STATE_TOPIC, json.dumps(value), qos=1, retain=True)
