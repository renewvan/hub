#!/usr/bin/env bash
# Stages 3 and 4 of the broker migration (docs/adr/0025). Run ON the Pi, in the hub directory, by a human who
# has already moved every driver and plugin and flashed the ESP32 (tickets 57 and 58).
#
#   bin/cutover-broker-acl.sh              stage 3: bin/check-broker-auth.sh --stage3 must find no anonymous
#                                          client; stage 4: drop the temporary `topic readwrite renewvan/#`
#                                          line (BROKER_ACL_OPEN=0), validate the new ACL in a throwaway
#                                          broker, SIGHUP, and check again. A failed check puts the line back.
#   bin/cutover-broker-acl.sh --rollback   put the line back (BROKER_ACL_OPEN=1), validate, SIGHUP.
#
# Rolling back is always safe: the line only widens write access, and no client depends on it being absent.
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
BROKER_CONTAINER="${BROKER_CONTAINER:-renewvan-mosquitto}"
HERE="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=bin/broker-accounts.sh
source "${HERE}/broker-accounts.sh"
[[ -f "${ENV_FILE}" ]] || { echo "ERROR: ${ENV_FILE} not found (run in the hub dir)" >&2; exit 2; }

apply_acl() { # <0|1>: regenerate the ACL with the line on/off, validate, reload
  set_var BROKER_ACL_OPEN "$1"
  bash "${HERE}/ensure-secrets.sh" "${ENV_FILE}"
  bash "${HERE}/validate-broker-config.sh"
  docker kill -s HUP "${BROKER_CONTAINER}" >/dev/null
}

if [[ "${1:-}" == "--rollback" ]]; then
  apply_acl 1
  echo "==> Rolled back: the temporary readwrite line is in the ACL again"
  exit 0
fi

echo "==> Stage 3: no anonymous client may remain"
bash "${HERE}/check-broker-auth.sh" --stage3 || {
  echo "ERROR: stage 3 failed; the ACL is unchanged. Move the clients listed above, then re-run." >&2
  exit 1
}

echo "==> Stage 4: removing the temporary readwrite line"
if ! apply_acl 0; then
  echo "ERROR: could not apply the closed ACL; restoring the migration line" >&2
  apply_acl 1 || true
  exit 1
fi
if bash "${HERE}/check-broker-auth.sh"; then
  echo "==> Cutover done: anonymous clients are read-only"
else
  echo "ERROR: the checks failed with the closed ACL; restoring the migration line" >&2
  apply_acl 1
  exit 1
fi
