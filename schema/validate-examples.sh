#!/usr/bin/env bash
# Validates every fixture under schema/examples/ against its entity schema.
# *.valid.json fixtures must pass; *.invalid.json fixtures must fail.
set -euo pipefail
cd "$(dirname "$0")"

status=0

# Schemas that are not bus entities: the demo envelope, host-plugin topics, and
# the Node manifest/info contracts. Keep in step with NON_ENTITY_PREFIXES in
# validate-demo.py.
is_entity() {
  case "$1" in
    demo | kiosk-* | tailscale-* | node-*) return 1 ;;
    *) return 0 ;;
  esac
}

# The `publishes` enum in the Node manifest/info schemas is static: fail when an
# entity schema exists that it does not list, or when it lists one with no schema.
check_publishes_enum() {
  local node_schema="$1" entities="" schema entity
  for schema in *.schema.json; do
    entity="${schema%.schema.json}"
    if is_entity "$entity"; then entities+="${entity}"$'\n'; fi
  done
  if ENTITIES="$entities" node -e '
    const fs = require("fs");
    const listed = new Set(JSON.parse(fs.readFileSync(process.argv[1], "utf8")).properties.publishes.items.enum);
    const actual = new Set(process.env.ENTITIES.split("\n").filter(Boolean));
    const missing = [...actual].filter((e) => !listed.has(e));
    const unknown = [...listed].filter((e) => !actual.has(e));
    if (missing.length) console.error(`entity schema(s) not in publishes enum: ${missing.join(", ")}`);
    if (unknown.length) console.error(`publishes enum lists entity with no schema: ${unknown.join(", ")}`);
    process.exit(missing.length || unknown.length ? 1 : 0);
  ' "$node_schema"; then
    echo "PASS  $node_schema publishes enum matches the entity schemas"
  else
    echo "FAIL  $node_schema publishes enum is out of sync with the entity schemas"
    status=1
  fi
}

check_publishes_enum node-manifest.schema.json
check_publishes_enum node-info.schema.json
for schema in *.schema.json; do
  entity="${schema%.schema.json}"
  # The demo envelope has no examples/ fixtures; validate-demo.py owns demo/.
  [ "$entity" = "demo" ] && continue
  for fixture in examples/"${entity}"*.valid.json; do
    [ -e "$fixture" ] || continue
    if npx --yes ajv-cli@5 test -s "$schema" -d "$fixture" --valid >/dev/null; then
      echo "PASS  $fixture validates against $schema"
    else
      echo "FAIL  $fixture should validate against $schema but did not"
      status=1
    fi
  done
  for fixture in examples/"${entity}"*.invalid.json; do
    [ -e "$fixture" ] || continue
    if npx --yes ajv-cli@5 test -s "$schema" -d "$fixture" --invalid >/dev/null; then
      echo "PASS  $fixture correctly rejected by $schema"
    else
      echo "FAIL  $fixture should be rejected by $schema but was not"
      status=1
    fi
  done
done

exit "$status"
