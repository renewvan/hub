"""
Tests for shared_setting: one persisted shared hub setting, its lifecycle, and the schema check.

The lifecycle is tested once here on a toy setting; test_hub_status.py tests only what each real
setting declares (its schema, default and how a command merges into the state).

Run: cd plugins/hub && python3 -m unittest test_shared_setting -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import shared_setting
from shared_setting import SharedSetting, compile_schema, set_entry

REPO = Path(__file__).resolve().parents[2]


class _FakeClient:
    def __init__(self):
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str, bool]] = []

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def _raw(payload) -> bytes:
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode()


LIST_SCHEMA = {
    "title": "toy-list",
    "type": "array",
    "maxItems": 3,
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1},
}
MAP_SCHEMA = {
    "title": "toy-map",
    "type": "object",
    "propertyNames": {"pattern": "^[a-z]+$"},
    "additionalProperties": {"type": "string", "minLength": 1},
}
MAP_SET_SCHEMA = {
    "title": "toy-map-set",
    "type": "object",
    "properties": {"k": {"type": "string"}, "v": {"type": ["string", "null"]}},
    "required": ["k", "v"],
    "additionalProperties": False,
}


class _Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # A directory that does not exist yet: the setting must create it.
        self.dir = Path(tmp.name) / "state"
        self.client = _FakeClient()
        self.list = SharedSetting(
            "toy-list", LIST_SCHEMA, ["a", "b"], path=self.dir / "toy-list.json"
        )
        self.map = SharedSetting(
            "toy-map",
            MAP_SCHEMA,
            {},
            path=self.dir / "toy-map.json",
            command_schema=MAP_SET_SCHEMA,
            merge=set_entry("k", "v"),
        )

    def store(self, setting, raw: bytes):
        setting.path.parent.mkdir(parents=True, exist_ok=True)
        setting.path.write_bytes(raw)

    def states(self, setting, client=None):
        return [p for p in (client or self.client).published if p[0] == setting.topic]


class TopicsAndPathTests(_Fixture):
    def test_topics_are_renewvan_hub_name_and_its_set(self):
        self.assertEqual(self.list.topic, "renewvan/hub/toy-list")
        self.assertEqual(self.list.set_topic, "renewvan/hub/toy-list/set")

    def test_the_default_path_comes_from_the_environment(self):
        with mock.patch.dict(shared_setting.os.environ, {"TOY_LIST_PATH": "/x/y.json"}):
            setting = SharedSetting("toy-list", LIST_SCHEMA, [])
        self.assertEqual(setting.path, Path("/x/y.json"))
        with mock.patch.dict(shared_setting.os.environ, clear=True):
            setting = SharedSetting("toy-list", LIST_SCHEMA, [])
        self.assertEqual(setting.path, Path("/var/lib/renewvan-hub/toy-list.json"))

    def test_a_default_that_breaks_its_own_schema_is_refused(self):
        with self.assertRaises(ValueError):
            SharedSetting("toy-list", LIST_SCHEMA, ["a", "a"], path=self.dir / "x")


class LifecycleTests(_Fixture):
    def test_connect_subscribes_and_publishes_the_default_retained_without_persisting_it(self):
        self.list.connect(self.client)
        self.assertEqual(self.client.subscribed, [self.list.set_topic])
        self.assertEqual(self.states(self.list), [(self.list.topic, '["a", "b"]', True)])
        self.assertFalse(self.list.path.exists(), "a default is not a choice")

    def test_connect_publishes_the_persisted_value(self):
        self.store(self.list, b'["c"]')
        self.list.connect(self.client)
        self.assertEqual(self.states(self.list), [(self.list.topic, '["c"]', True)])

    def test_every_reconnect_subscribes_and_publishes_again(self):
        self.list.connect(self.client)
        self.list.connect(self.client)
        self.assertEqual(len(self.states(self.list)), 2)
        self.assertEqual(self.client.subscribed.count(self.list.set_topic), 2)

    def test_a_valid_command_persists_and_republishes_retained(self):
        self.assertTrue(self.list.command(self.client, _raw(["c", "d"])))
        self.assertEqual(json.loads(self.list.path.read_text()), ["c", "d"])
        self.assertEqual(self.states(self.list), [(self.list.topic, '["c", "d"]', True)])

    def test_a_command_replaces_the_whole_value(self):
        self.list.command(self.client, _raw(["c", "d"]))
        self.list.command(self.client, _raw(["e"]))
        self.assertEqual(self.list.load(), ["e"])

    def test_a_persisted_command_survives_a_restart(self):
        self.list.command(self.client, _raw(["c"]))
        restarted = _FakeClient()
        self.list.connect(restarted)
        self.assertEqual(self.states(self.list, restarted), [(self.list.topic, '["c"]', True)])

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        self.list.save(["c"])
        self.list.save(["d"])
        self.assertEqual([p.name for p in self.dir.iterdir()], [self.list.path.name])
        self.assertEqual(self.list.path.read_text(), '["d"]')

    def test_invalid_commands_are_ignored_and_change_nothing(self):
        self.list.command(self.client, _raw(["c"]))
        self.client.published.clear()
        for payload in (b"", b"not json", b"\xff\xfe", b"{}", ["c", "c"], [""], [1], list("wxyz")):
            with self.subTest(payload=payload):
                self.assertFalse(self.list.command(self.client, _raw(payload)))
        self.assertEqual(self.client.published, [])
        self.assertEqual(json.loads(self.list.path.read_text()), ["c"])

    def test_an_invalid_command_with_nothing_persisted_writes_nothing(self):
        self.list.command(self.client, b"3")
        self.assertFalse(self.list.path.exists())

    def test_unreadable_persisted_value_falls_back_to_the_default(self):
        for bad in (b"", b"not json", b"\xff\xfe", b'"x"', b"{}", b'["a", "a"]', b"[3]"):
            with self.subTest(bad=bad):
                self.store(self.list, bad)
                self.assertEqual(self.list.load(), ["a", "b"])

    def test_the_default_is_never_shared_with_the_caller(self):
        self.list.load().append("z")
        self.assertEqual(self.list.load(), ["a", "b"])

    def test_unwritable_state_is_not_applied_or_published(self):
        with mock.patch.object(self.list, "save", side_effect=OSError("ro")):
            self.assertFalse(self.list.command(self.client, _raw(["c"])))
        self.assertEqual(self.client.published, [])


class MergedCommandTests(_Fixture):
    def _set(self, k, v):
        return self.map.command(self.client, _raw({"k": k, "v": v}))

    def test_a_command_merges_one_entry_and_republishes_the_whole_map(self):
        self._set("a", "x")
        self._set("b", "y")
        self.assertEqual(json.loads(self.map.path.read_text()), {"a": "x", "b": "y"})
        self.assertEqual(json.loads(self.states(self.map)[-1][1]), {"a": "x", "b": "y"})
        self.assertTrue(self.states(self.map)[-1][2])

    def test_a_later_command_replaces_the_entry_and_null_clears_it(self):
        self._set("a", "x")
        self._set("b", "y")
        self._set("a", "z")
        self.assertEqual(self.map.load(), {"a": "z", "b": "y"})
        self._set("a", None)
        self.assertEqual(self.map.load(), {"b": "y"})

    def test_clearing_an_entry_that_is_not_there_is_harmless(self):
        self.assertTrue(self._set("a", None))
        self.assertEqual(json.loads(self.map.path.read_text()), {})

    def test_the_command_is_checked_against_its_own_schema(self):
        for payload in (
            {"k": "a"},
            {"k": "a", "v": "x", "extra": 1},
            ["a", "x"],
            {"k": 1, "v": "x"},
        ):
            with self.subTest(payload=payload):
                self.assertFalse(self.map.command(self.client, _raw(payload)))
        self.assertEqual(self.client.published, [])

    def test_a_merge_that_breaks_the_state_schema_is_ignored(self):
        # the command schema allows any key; the state only lower-case ones
        self.assertFalse(self._set("A", "x"))
        self.assertFalse(self._set("a", ""))
        self.assertFalse(self.map.path.exists())

    def test_one_bad_stored_entry_does_not_cost_the_others(self):
        self.store(self.map, b'{"a": "x", "B": "y", "c": "", "d": 3}')
        self.assertEqual(self.map.load(), {"a": "x"})

    def test_a_stored_value_that_is_not_a_map_falls_back_to_the_default(self):
        for bad in (b"[]", b'"x"', b"null"):
            with self.subTest(bad=bad):
                self.store(self.map, bad)
                self.assertEqual(self.map.load(), {})


class CompileSchemaTests(unittest.TestCase):
    def test_an_unknown_keyword_is_refused_so_nothing_is_silently_unchecked(self):
        for schema in ({"type": "string", "format": "email"}, {"items": {"minimum": 3}}):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                compile_schema(schema)

    def test_annotations_are_allowed(self):
        check = compile_schema({"$schema": "x", "$id": "y", "title": "t", "description": "d"})
        self.assertTrue(check(3))

    def test_a_pattern_end_anchor_does_not_accept_a_trailing_newline(self):
        # JSON Schema patterns are ECMA 262, where `$` is the end of the string; Python's `$`
        # also matches before a final newline.
        check = compile_schema({"type": "string", "pattern": "^\\S(.*\\S)?$"})
        self.assertTrue(check("Lights"))
        self.assertFalse(check("Lights\n"))
        self.assertFalse(check("Li\nghts"))

    def test_a_pattern_is_unanchored_unless_it_says_so(self):
        self.assertTrue(compile_schema({"pattern": "b"})("abc"))

    def test_a_boolean_is_not_a_string_and_null_is_its_own_type(self):
        check = compile_schema({"type": ["string", "null"]})
        self.assertTrue(check(None))
        self.assertFalse(check(True))


try:
    import jsonschema
except ImportError:  # CI installs it; a dev machine may not
    jsonschema = None

# Payloads the hub settings must judge exactly as the schema does: every schema example plus
# the edges of each rule. No string ends in a newline: there the Python jsonschema library
# follows Python's `$`, not JSON Schema's (see test_a_pattern_end_anchor_...).
CORPUS = [
    "",
    "auto",
    "512",
    3,
    True,
    None,
    [],
    {},
    ["Lights"],
    ["Lights", "Lights"],
    [" Lights"],
    ["x" * 32],
    ["x" * 33],
    [f"g{i}" for i in range(16)],
    [f"g{i}" for i in range(17)],
    [f"r-{i}" for i in range(512)],
    [f"r-{i}" for i in range(513)],
    ["a", "b/c"],
    ["a b"],
    ["a+"],
    [3],
    [None],
    [["a"]],
    {"tank/fresh": "Droplet"},
    {"tank/fresh": "droplet"},
    {"Tank/fresh": "Droplet"},
    {"tank/fresh/x": "Droplet"},
    {"tank/fresh": 3},
    {"relay-x8-01": "Lights"},
    {"relay/01": "Lights"},
    {"relay-x8-01": ""},
    {"relay-x8-01": "Water pump"},
    {"key": "tank/fresh", "icon": "Droplet"},
    {"key": "tank/fresh", "icon": None},
    {"key": "tank/fresh", "icon": ""},
    {"key": "tank/fresh"},
    {"key": "tank/fresh", "icon": "Droplet", "extra": 1},
    {"id": "relay-x8-01", "group": "Lights"},
    {"id": "relay-x8-01", "group": None},
    {"id": "relay-x8-01", "group": " Lights"},
    {"id": "relay-x8-01", "group": "x" * 33},
    {"id": "", "group": "Lights"},
    {"id": "relay-x8-01"},
    {"group": "Lights"},
]


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class AgreesWithTheSchemaTests(unittest.TestCase):
    """The plugin host has no jsonschema; the checks it compiles from the schema files must
    judge every payload as jsonschema does."""

    NAMES = (
        "hub-primary-battery",
        "hub-icons",
        "hub-icon-set",
        "hub-relay-groups",
        "hub-relay-group-set",
        "hub-relay-group-list",
        "hub-relay-order",
    )

    def test_every_setting_schema_compiles_and_agrees_with_jsonschema(self):
        for name in self.NAMES:
            schema = shared_setting.read_schema(name)
            check = compile_schema(schema)
            reference = jsonschema.Draft7Validator(schema)
            examples = [
                json.loads(p.read_text())
                for p in sorted((REPO / "schema" / "examples").glob(f"{name}.*json"))
            ]
            for value in CORPUS + examples:
                with self.subTest(schema=name, value=value):
                    self.assertEqual(check(value), reference.is_valid(value))

    def test_the_schema_examples_exist_for_the_settings_that_have_them(self):
        found = {p.name.split(".")[0] for p in (REPO / "schema" / "examples").glob("hub-*.json")}
        self.assertTrue({"hub-primary-battery", "hub-icons", "hub-icon-set"} <= found)


class ReadSchemaTests(unittest.TestCase):
    def test_reads_from_the_checkout_and_an_override_directory(self):
        self.assertEqual(shared_setting.read_schema("hub-icons")["title"], "hub-icons")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x.schema.json").write_text('{"title": "x"}')
            self.assertEqual(shared_setting.read_schema("x", Path(tmp)), {"title": "x"})


if __name__ == "__main__":
    unittest.main()
