"""
One persisted shared hub setting: a retained state topic renewvan/hub/<name>, a command topic
renewvan/hub/<name>/set, a default, a file on the Pi, and the JSON Schema that says what shape the
state (and the command) may have.

The hub plugin owns each setting and checks only the shape (CONTEXT.md, "Entity icons"): whether an
id names an instance on the bus is the consumer's call. A command is checked, merged into the state
(or replaces it), checked again, persisted atomically and only then republished retained: a value
that would silently revert on restart is worse than visibly not applying it. On every (re)connect
the persisted value, or the default, is published again. The default is never written to disk.

The plugin host runs no jsonschema (the venv holds paho-mqtt only), so compile_schema() turns the
schema file into a check for the small set of keywords the hub schemas use, and refuses any other
keyword so nothing is silently left unchecked. test_shared_setting.py proves it judges like
jsonschema.
"""

import copy
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
# bin/deploy.sh ships the hub-*.schema.json files into plugins/hub/schema/ on the Pi; a checkout
# reads the repo's schema/ directly.
SCHEMA_DIR = Path(
    os.environ.get(
        "HUB_SCHEMA_DIR",
        _HERE / "schema" if (_HERE / "schema").is_dir() else _HERE.parents[1] / "schema",
    )
)
STATE_DIR = Path("/var/lib/renewvan-hub")  # StateDirectory= of renewvan-hub.service


def read_schema(name: str, schema_dir: Path | None = None) -> dict:
    """The JSON Schema schema/<name>.schema.json."""
    return json.loads(((schema_dir or SCHEMA_DIR) / f"{name}.schema.json").read_text())


# --- the schema check --------------------------------------------------------------------------

_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_TYPES: dict[str, Callable[[object], bool]] = {
    "string": lambda v: isinstance(v, str),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}

Check = Callable[[object], bool]


def _pattern(source: str) -> re.Pattern:
    # ECMA 262 `$` (no multiline flag) is the end of the string; Python's `$` also matches before
    # a final newline, so an end anchor becomes \Z.
    if source.endswith("$") and not source.endswith("\\$"):
        source = source[:-1] + r"\Z"
    return re.compile(source)


def compile_schema(schema: dict) -> Check:
    """A predicate for `schema`. Raises ValueError on a keyword it does not implement."""
    checks: list[Check] = []
    for keyword, arg in schema.items():
        if keyword in _ANNOTATIONS:
            continue
        if keyword == "type":
            names = arg if isinstance(arg, list) else [arg]
            unknown = [n for n in names if n not in _TYPES]
            if unknown:
                raise ValueError(f"unsupported type {unknown}")
            tests = [_TYPES[n] for n in names]
            checks.append(lambda v, tests=tests: any(t(v) for t in tests))
        elif keyword == "minLength":
            checks.append(lambda v, n=arg: not isinstance(v, str) or len(v) >= n)
        elif keyword == "maxLength":
            checks.append(lambda v, n=arg: not isinstance(v, str) or len(v) <= n)
        elif keyword == "pattern":
            checks.append(
                lambda v, p=_pattern(arg): not isinstance(v, str) or p.search(v) is not None
            )
        elif keyword == "maxItems":
            checks.append(lambda v, n=arg: not isinstance(v, list) or len(v) <= n)
        elif keyword == "uniqueItems":
            if arg:
                checks.append(lambda v: not isinstance(v, list) or _unique(v))
        elif keyword == "items":
            checks.append(
                lambda v, c=compile_schema(arg): not isinstance(v, list) or all(map(c, v))
            )
        elif keyword == "required":
            checks.append(lambda v, r=arg: not isinstance(v, dict) or all(k in v for k in r))
        elif keyword == "propertyNames":
            checks.append(
                lambda v, c=compile_schema(arg): not isinstance(v, dict) or all(map(c, v))
            )
        elif keyword in ("properties", "additionalProperties"):
            pass  # together, below
        else:
            raise ValueError(f"unsupported schema keyword {keyword!r}")
    if "properties" in schema or "additionalProperties" in schema:
        props = {k: compile_schema(s) for k, s in schema.get("properties", {}).items()}
        extra = schema.get("additionalProperties", True)
        rest = extra if isinstance(extra, bool) else compile_schema(extra)
        checks.append(lambda v: not isinstance(v, dict) or _object_ok(v, props, rest))
    return lambda value: all(check(value) for check in checks)


def _unique(items: list) -> bool:
    seen = [json.dumps(i, sort_keys=True) for i in items]
    return len(set(seen)) == len(seen)


def _object_ok(value: dict, props: dict[str, Check], rest: Check | bool) -> bool:
    for key, item in value.items():
        check = props.get(key, rest)
        if check is False or (check is not True and not check(item)):
            return False
    return True


# --- the setting ---------------------------------------------------------------------------------

Merge = Callable[[object, object], object]


def set_entry(key_field: str, value_field: str) -> Merge:
    """A command {key_field: k, value_field: v} sets entry k of a map state to v; null clears it."""

    def merge(state: dict, command: dict) -> dict:
        state = dict(state)
        if command[value_field] is None:
            state.pop(command[key_field], None)
        else:
            state[command[key_field]] = command[value_field]
        return state

    return merge


def _decode(raw: bytes) -> tuple[bool, object]:
    try:
        return True, json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return False, None


def _describe(value: object) -> str:
    if isinstance(value, (list, dict)):
        return f"{len(value)} entr{'y' if len(value) == 1 else 'ies'}"
    return json.dumps(value)


class SharedSetting:
    """A persisted shared setting, declared by its name, state schema and default.

    Without a command schema a command is a whole new state, checked against the state schema.
    With one, the command is checked against it and `merge(state, command)` gives the new state.
    The file is <NAME>_PATH from the environment, else /var/lib/renewvan-hub/<name>.json.
    """

    def __init__(
        self,
        name: str,
        schema: dict,
        default: object,
        *,
        path: Path | None = None,
        command_schema: dict | None = None,
        merge: Merge | None = None,
    ):
        if (command_schema is None) != (merge is None):
            raise ValueError("a command schema and a merge go together")
        self.name = name
        self.topic = f"renewvan/hub/{name}"
        self.set_topic = f"{self.topic}/set"
        env = name.upper().replace("-", "_") + "_PATH"
        self.path = Path(path or os.environ.get(env, STATE_DIR / f"{name}.json"))
        self.schema = schema
        self.command_schema = command_schema or schema
        self._valid = compile_schema(schema)
        self._valid_command = compile_schema(self.command_schema)
        self._merge = merge
        self._per_entry = schema.get("type") == "object"
        if not self._valid(default):
            raise ValueError(f"the default of {name} breaks its own schema")
        self._default = default

    def default(self) -> object:
        return copy.deepcopy(self._default)

    def load(self) -> object:
        """The persisted value, or the default when nothing valid is stored.

        In a map state an entry of the wrong shape is dropped on its own: one bad entry never costs
        the rest.
        """
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return self.default()
        except OSError as exc:
            log.error("Cannot read %s: %s — using the default %s", self.path, exc, self.name)
            return self.default()
        ok, value = _decode(raw)
        if ok and self._per_entry and isinstance(value, dict):
            value = {k: v for k, v in value.items() if self._valid({k: v})}
        if not ok or not self._valid(value):
            log.warning("Ignoring invalid %s — using the default %s", self.path, self.name)
            return self.default()
        return value

    def save(self, value: object) -> None:
        """Replace the file atomically: a crash mid-write never leaves a torn file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(value))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def publish(self, client, value: object) -> None:
        client.publish(self.topic, json.dumps(value), qos=1, retain=True)

    def connect(self, client) -> None:
        """On every (re)connect: listen for commands and publish the current value retained."""
        client.subscribe(self.set_topic, qos=1)
        value = self.load()
        self.publish(client, value)
        log.info("Published %s: %s", self.name, _describe(value))

    def parse(self, raw: bytes) -> object | None:
        """The new state a command payload asks for, None when it is invalid."""
        ok, command = _decode(raw)
        if not ok or not self._valid_command(command):
            return None
        value = command if self._merge is None else self._merge(self.load(), command)
        return value if self._valid(value) else None

    def command(self, client, raw: bytes) -> bool:
        """Apply a command payload: persist, then republish. False when it was not applied."""
        value = self.parse(raw)
        if value is None:
            log.warning(
                "Ignoring invalid %s command %r (want schema/%s.schema.json)",
                self.name,
                raw,
                self.command_schema.get("title", self.name),
            )
            return False
        try:
            self.save(value)
        except OSError as exc:
            log.error("Cannot persist %s to %s: %s — not applied", self.name, self.path, exc)
            return False
        self.publish(client, value)
        log.info("%s set: %s", self.name, _describe(value))
        return True
