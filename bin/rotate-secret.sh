#!/usr/bin/env bash
# Rotate one broker credential without touching any other client (docs/adr/0025).
#
#   bin/rotate-secret.sh <client>     tank gps temperature tilt wattcycle autoterm vedirect logger venus
#                                     router demo kiosk tailscale hub command, or `login` (the dashboard login)
#
# Regenerates that client's password in .env, rewrites the password file, updates the client's own
# credential file (config.ini or env file), SIGHUPs the broker, and restarts the client. Run on the Pi in
# the hub directory (next to docker-compose.yml and .env). The new dashboard login password (and every signed-in device is signed out) is shown once.
# A board account (`esp32-<name>`) is refused: its password is compiled into the firmware, so it is rotated only by OTA with a new
# secrets.yaml (skill renewvan-flash-esp32), and only on suspected compromise.
# A client that is refused with rc=5 does not retry by itself, hence the restart.
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
KEY="${1:-}"
# shellcheck source=bin/broker-accounts.sh
source "$(dirname "${BASH_SOURCE[0]}")/broker-accounts.sh"

usage() {
  echo "usage: $0 <client|login>   clients: $(client_keys | grep -v '^esp32-' | tr '\n' ' ')" >&2
  exit 2
}
[[ -n "${KEY}" ]] || usage
[[ -f "${ENV_FILE}" ]] || { echo "ERROR: ${ENV_FILE} not found" >&2; exit 1; }

if [[ "${KEY}" == esp32 || "${KEY}" == esp32-* ]]; then
  echo "ERROR: ${KEY} is rotated by OTA only (new secrets.yaml, skill renewvan-flash-esp32)." >&2
  exit 2
fi

if [[ "${KEY}" == "login" ]]; then
  pw="$(gen 12)"
  set_var LOGIN_PASSWORD_HASH "$(login_hash "${pw}")"
  docker compose up -d --force-recreate --no-deps login # a restart would keep the old hash
  echo "==> Dashboard login rotated (shown once, only its hash is stored): user ${LOGIN_USER_NAME} password ${pw}"
  exit 0
fi

client_spec "${KEY}" >/dev/null || usage
# Keep .env and the password file in step: put the old password back if the new file cannot be written.
var="$(client_var "${KEY}")"
old_pw="$(get "${var}")"
set_var "${var}" "$(gen 16)"
if ! write_passwd_file; then
  set_var "${var}" "${old_pw}"
  echo "ERROR: could not write the password file; ${var} restored, nothing rotated" >&2
  exit 1
fi
apply_client_credential "${KEY}" >/dev/null
# SIGHUP reloads the password file; only a client whose credential stopped validating is dropped.
docker kill -s HUP "${BROKER_CONTAINER}" >/dev/null
restart_client "${KEY}"
echo "==> Rotated ${KEY} (new value in ${ENV_FILE} as $(client_var "${KEY}"), not shown)"
