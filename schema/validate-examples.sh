#!/usr/bin/env bash
# Validates every fixture under schema/examples/ against its entity schema.
# *.valid.json fixtures must pass; *.invalid.json fixtures must fail.
set -euo pipefail
cd "$(dirname "$0")"

status=0
for schema in *.schema.json; do
  entity="${schema%.schema.json}"
  for fixture in examples/"${entity}"*.valid.json; do
    [ -e "$fixture" ] || continue
    if npx --yes ajv-cli@5 test -s "$schema" -d "$fixture" --valid >/dev/null; then
      echo "PASS  $fixture validates against $schema"
    else
      echo "FAIL  $fixture should validate against $schema but did not"
      status=1
    fi
  done
  for fixture in examples/"${entity}".invalid.json; do
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
