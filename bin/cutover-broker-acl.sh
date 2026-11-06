#!/usr/bin/env bash
# Stages 3 and 4 of the broker migration (docs/adr/0025). Run ON the Pi, in the hub directory, by a human who
# has already moved every driver and plugin and flashed the ESP32 (tickets 57 and 58).
#
#   bin/cutover-broker-acl.sh              stage 3: bin/check-broker-auth.sh --stage3 must find no anonymous
#                                          client; stage 4: BROKER_ACL_OPEN=0 drops the temporary
#                                          `topic readwrite renewvan/#` line and sets `allow_anonymous
#                                          false`, validates the new files in a throwaway broker, restarts
#                                          the broker (refusing anonymous clients), waits for the clients to
#                                          reconnect and checks again. A failed check puts the migration
#                                          state back.
#   bin/cutover-broker-acl.sh --rollback   BROKER_ACL_OPEN=1: line back, anonymous allowed again; restart.
#
# The broker is RESTARTED, not sent SIGHUP: on the Pi a reload applied the new ACL and passwords but kept
# accepting anonymous clients after allow_anonymous became false. Every client reconnects by itself (the
# plugins' on_disconnect handlers were fixed for this); CUTOVER_RETRIES x CUTOVER_WAIT seconds is how long
# the final check waits for them.
#
# Rolling back is always safe: it only widens access, and no client depends on it being absent.
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
BROKER_CONTAINER="${BROKER_CONTAINER:-renewvan-mosquitto}"
HERE="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=bin/broker-accounts.sh
source "${HERE}/broker-accounts.sh"
[[ -f "${ENV_FILE}" ]] || { echo "ERROR: ${ENV_FILE} not found (run in the hub dir)" >&2; exit 2; }

CUTOVER_RETRIES="${CUTOVER_RETRIES:-9}"
CUTOVER_WAIT="${CUTOVER_WAIT:-10}"

wait_for_broker() { # the reader login answers: the broker is up and has read its files
  local i rc pw
  pw="$(get "$(client_var reader)")"
  for ((i = 0; i < 30; i++)); do
    rc=0
    docker exec "${BROKER_CONTAINER}" mosquitto_sub -h localhost -u reader -P "${pw}" -t 'renewvan/#' -W 1 -C 1 >/dev/null 2>&1 || rc=$?
    if [[ "${rc}" == "0" || "${rc}" == "27" ]]; then return 0; fi
    sleep 1
  done
  echo "ERROR: the broker did not come back within 30s" >&2
  return 1
}

apply_acl() { # <0|1>: regenerate the files with the migration state on/off, validate, restart
  set_var BROKER_ACL_OPEN "$1"
  bash "${HERE}/ensure-secrets.sh" "${ENV_FILE}"
  bash "${HERE}/validate-broker-config.sh"
  docker restart "${BROKER_CONTAINER}" >/dev/null
  # The restart read the new mosquitto.conf, so deploy must not restart the broker for it as well.
  rm -f "${RENEWVAN_ETC}/mosquitto/mosquitto.conf.changed"
  wait_for_broker
}

# The final check can fail only because a client has not reconnected yet: give it CUTOVER_RETRIES tries.
final_check() {
  local i
  for ((i = 1; i <= CUTOVER_RETRIES; i++)); do
    if bash "${HERE}/check-broker-auth.sh"; then return 0; fi
    if ((i < CUTOVER_RETRIES)); then
      echo "==> Not yet (try ${i}/${CUTOVER_RETRIES}); clients are still reconnecting, waiting ${CUTOVER_WAIT}s" >&2
      sleep "${CUTOVER_WAIT}"
    fi
  done
  return 1
}

if [[ "${1:-}" == "--rollback" ]]; then
  apply_acl 1
  echo "==> Rolled back: the temporary readwrite line is back and anonymous clients are allowed again"
  exit 0
fi

echo "==> Stage 3: no anonymous client may remain"
bash "${HERE}/check-broker-auth.sh" --stage3 || {
  echo "ERROR: stage 3 failed; the ACL is unchanged. Move the clients listed above, then re-run." >&2
  exit 1
}

echo "==> Stage 4: removing the temporary readwrite line and refusing anonymous clients"
if ! apply_acl 0; then
  echo "ERROR: could not apply the closed ACL; restoring the migration line" >&2
  apply_acl 1 || true
  exit 1
fi
if final_check; then
  echo "==> Cutover done: allow_anonymous is false; every client needs an account"
else
  echo "ERROR: the checks failed with the closed ACL; restoring the migration line" >&2
  apply_acl 1
  exit 1
fi
