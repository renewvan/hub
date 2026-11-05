#!/usr/bin/env bash
# Pre-reload validation of the broker's auth files (docs/adr/0025). Run on the Pi by bin/deploy.sh after
# bin/ensure-secrets.sh, before the running broker is reloaded.
#
# `mosquitto --test-config` does not read the ACL, and a bad ACL line makes the broker exit on SIGHUP, so
# this starts a throwaway broker (no network, scratch data dir) on the candidate mosquitto.conf plus the
# password and ACL files, and requires it to still be running after a few seconds. The running broker is
# never touched. On success the candidate becomes mosquitto.conf (mosquitto.conf.changed is left behind
# when its content changed, so deploy restarts instead of reloading). On failure the candidate is dropped
# and the previous passwd/acl are restored, so a reboot cannot bring the broker up on a bad file.
#
# mosquitto.conf is managed here: the repo's docker/mosquitto/config/mosquitto.conf plus
# docker/mosquitto/auth.conf, rebuilt on every deploy (hand edits to the installed copy are replaced).
#
# Usage: bin/validate-broker-config.sh        (cwd = the hub dir)  Exit 0 = valid, 1 = invalid.
set -euo pipefail

RENEWVAN_ETC="${RENEWVAN_ETC:-/etc/renewvan}"
DIR="${RENEWVAN_ETC}/mosquitto"
BASE_CONF="docker/mosquitto/config/mosquitto.conf"
AUTH_CONF="docker/mosquitto/auth.conf"
SECONDS_UP="${VALIDATE_BROKER_SECONDS:-4}"

for f in "${BASE_CONF}" "${AUTH_CONF}" "${DIR}/passwd" "${DIR}/acl"; do
  [[ -f "${f}" ]] || { echo "ERROR: ${f} not found (run bin/ensure-secrets.sh first)" >&2; exit 1; }
done

image="$(awk '$0 == "  mosquitto:" {on = 1; next} on && /^  [a-z]/ {exit} on && $1 == "image:" {print $2; exit}' docker-compose.yml)"
image="${MOSQUITTO_IMAGE:-${image}}"
[[ -n "${image}" ]] || { echo "ERROR: cannot find the mosquitto image in docker-compose.yml" >&2; exit 1; }

candidate="${DIR}/mosquitto.conf.new"
{ cat "${BASE_CONF}"; echo; cat "${AUTH_CONF}"; } >"${candidate}"
chmod 640 "${candidate}"

rollback() {
  rm -f "${candidate}"
  for f in passwd acl; do
    if [[ -f "${DIR}/${f}.prev" ]]; then cp -p "${DIR}/${f}.prev" "${DIR}/${f}"; fi
  done
}

# Exit 124 = still running when timeout stopped it = it loaded everything. Any other code is a startup
# failure. The mount is read-only, so the throwaway broker cannot change the files it checks.
rc=0
docker run --rm --network none --tmpfs /mosquitto/data -v "${DIR}:/mosquitto/config:ro" "${image}" \
  timeout "${SECONDS_UP}" mosquitto -c /mosquitto/config/mosquitto.conf.new >/dev/null 2>&1 || rc=$?
if [[ "${rc}" != "124" ]]; then
  # Re-run once with output so the operator sees mosquitto's own message (e.g. "Invalid line in acl file").
  docker run --rm --network none --tmpfs /mosquitto/data -v "${DIR}:/mosquitto/config:ro" "${image}" \
    timeout "${SECONDS_UP}" mosquitto -c /mosquitto/config/mosquitto.conf.new 2>&1 | tail -n 15 >&2 || true
  rollback
  echo "ERROR: the broker did not start on the new password/ACL files (exit ${rc}); previous files restored, running broker untouched." >&2
  exit 1
fi

if ! cmp -s "${candidate}" "${DIR}/mosquitto.conf"; then
  : >"${DIR}/mosquitto.conf.changed"
fi
mv "${candidate}" "${DIR}/mosquitto.conf"
echo "==> Broker config and ACL validated"
