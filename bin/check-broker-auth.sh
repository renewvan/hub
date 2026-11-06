#!/usr/bin/env bash
# Run ON the Pi, in the hub directory. Proves docs/adr/0025's broker rules by their effect (a denied publish
# is silent on MQTT 3.1.1, so each check publishes as one account and watches for delivery on a plain
# subscriber; it never trusts an error message).
#
#   1. Every client whose process runs is connected with its OWN account (broker log, `u'<key>'`).
#   2. `command` can write a set topic; the demo driver cannot write renewvan/demo/enabled/set; a driver
#      cannot write another client's set or state topics; the ESP32 cannot write a set topic.
#   3. --stage3: no anonymous client is connected (the cutover gate: after it they are refused). Clients
#      you know are harmless can be excluded with CHECK_ALLOW_ANON=<regex on the client id>.
#   4. When the broker is closed (BROKER_ACL_OPEN=0), an anonymous client cannot even connect.
#
# Probes use unknown ids (renewvan/relay/zz-check-probe/...) that no device listens to, and unretained
# payloads, so nothing real is switched. The demo toggle is re-published with its CURRENT value.
#
# Usage: bin/check-broker-auth.sh [--stage3]      Exit 0 = every check passed, 1 = at least one failed.
# No -e on purpose: every check runs and failures accumulate into the exit code.
set -uo pipefail

ENV_FILE="${ENV_FILE:-.env}"
BROKER_CONTAINER="${BROKER_CONTAINER:-renewvan-mosquitto}"
STAGE3=0
[[ "${1:-}" == "--stage3" ]] && STAGE3=1
# shellcheck source=bin/broker-accounts.sh
source "$(dirname "${BASH_SOURCE[0]}")/broker-accounts.sh"

FAILED=0
pass() { printf 'ok    %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; FAILED=1; }
note() { printf 'note  %s\n' "$*"; }

[[ -f "${ENV_FILE}" ]] || { echo "ERROR: ${ENV_FILE} not found (run in the hub dir)" >&2; exit 2; }

# The subscriber logs in as `reader` (it works before and after anonymous clients are refused) and the
# publisher is one account at a time.
READER_PW="$(get "$(client_var reader)")"

# probe <user|anonymous> <topic> <payload>: prints the payload when the broker delivered the publish to the
# reader's subscription (so the publisher was allowed to write the topic), nothing otherwise.
probe() {
  local user="$1" topic="$2" payload="$3" pw=""
  [[ "${user}" == "anonymous" ]] || pw="$(get "$(client_var "${user}")")"
  PROBE_USER="${user}" PROBE_PW="${pw}" PROBE_TOPIC="${topic}" PROBE_MSG="${payload}" PROBE_READER_PW="${READER_PW}" \
    docker exec -e PROBE_USER -e PROBE_PW -e PROBE_TOPIC -e PROBE_MSG -e PROBE_READER_PW "${BROKER_CONTAINER}" sh -c '
      out=/tmp/probe.$$
      mosquitto_sub -h localhost -u reader -P "$PROBE_READER_PW" -t "$PROBE_TOPIC" -W 4 -C 1 >"$out" 2>/dev/null &
      sleep 1
      if [ "$PROBE_USER" = anonymous ]; then
        mosquitto_pub -h localhost -t "$PROBE_TOPIC" -m "$PROBE_MSG" 2>/dev/null
      else
        mosquitto_pub -h localhost -u "$PROBE_USER" -P "$PROBE_PW" -t "$PROBE_TOPIC" -m "$PROBE_MSG" 2>/dev/null
      fi
      wait
      cat "$out"; rm -f "$out"' 2>/dev/null
}

expect_allowed() { # <user> <topic> <payload>
  if [[ -n "$(probe "$1" "$2" "$3")" ]]; then pass "$1 can write $2"; else fail "$1 cannot write $2 (it must)"; fi
}
expect_denied() { # <user> <topic> <payload>
  if [[ -z "$(probe "$1" "$2" "$3")" ]]; then pass "$1 cannot write $2"; else fail "$1 CAN write $2 (it must not)"; fi
}

PROBE_RELAY="renewvan/relay/zz-check-probe"

# A failed probe must not read as a denial: the broker has to answer first.
# Log in as the reader and read renewvan/# ($SYS is not readable). mosquitto_sub exits 27 when it stayed
# connected until -W ran out (nothing retained), 0 when a message arrived; anything else is a connection
# failure (5 = refused, so this also proves the reader account itself works).
reach_rc=0
docker exec "${BROKER_CONTAINER}" mosquitto_sub -h localhost -u reader -P "${READER_PW}" -t 'renewvan/#' -W 3 -C 1 >/dev/null 2>&1 || reach_rc=$?
if [[ "${reach_rc}" != "0" && "${reach_rc}" != "27" ]]; then
  echo "ERROR: cannot read from the broker in ${BROKER_CONTAINER} as 'reader' (rc=${reach_rc}); no denial can be proven" >&2
  exit 1
fi

# --- 1. Connected accounts ------------------------------------------------------------------------
# The log has one "New client connected ... (p2, c1, k60, u'name')" per connection; the last event per
# client id decides whether it is still connected.
# Only the log since the broker last started counts: the log survives a restart, and a connection from
# before it never logs a disconnect, so it would read as still connected forever.
connections() {
  local since=() started
  started="$(docker inspect -f '{{.State.StartedAt}}' "${BROKER_CONTAINER}" 2>/dev/null || true)"
  [[ -z "${started}" ]] || since=(--since "${started}")
  docker logs "${since[@]}" "${BROKER_CONTAINER}" 2>&1 | awk '
    /New client connected from/ {
      id = $0; sub(/.* as /, "", id); sub(/ \(.*/, "", id)
      user = ""
      if (match($0, /u\047[^\047]*\047/)) user = substr($0, RSTART + 2, RLENGTH - 3)
      state[id] = "up"; who[id] = user; next
    }
    / Client [^ ]+ (\[[^]]*\] )?(closed its connection|disconnected|has exceeded timeout)/ {
      id = $0; sub(/.*Client /, "", id); sub(/ .*/, "", id); state[id] = "down"
    }
    END { for (id in state) if (state[id] == "up") print id "\t" who[id] }'
}

running_clients() { # keys whose process runs now
  local key spec kind target unit services
  services="$(docker compose ps --status running --services 2>/dev/null || true)"
  for key in $(client_keys); do
    spec="$(client_spec "${key}")"
    IFS=: read -r _ kind target unit <<<"${spec}"
    case "${kind}" in
      config.ini | compose) grep -qx "${target}" <<<"${services}" && echo "${key}" ;;
      envfile) systemctl is-active --quiet "${unit}" 2>/dev/null && echo "${key}" ;;
    esac
  done
}

conns="$(connections)"
users="$(cut -f2 <<<"${conns}" | sort -u)"
for key in $(running_clients); do
  if grep -qx "${key}" <<<"${users}"; then pass "${key} is connected as ${key}"; else fail "${key} runs but no connection as '${key}' is in the broker log (restart it? rc=5 does not retry)"; fi
done
grep -qx esp32 <<<"${users}" && pass "esp32 is connected as esp32" || note "esp32 not seen connected (offline, or not flashed with its account yet)"

# --- 3. Anonymous clients (stage 3 gate) ------------------------------------------------------------
anon="$(awk -F'\t' '$2 == "" {print $1}' <<<"${conns}" | { grep -Ev "${CHECK_ALLOW_ANON:-^$}" || true; })"
if [[ -n "${anon}" ]]; then
  if [[ "${STAGE3}" == "1" ]]; then fail "anonymous clients still connected: $(tr '\n' ' ' <<<"${anon}")"; else note "anonymous clients connected: $(tr '\n' ' ' <<<"${anon}")"; fi
elif [[ "${STAGE3}" == "1" ]]; then
  pass "no anonymous client is connected"
fi

# --- 2. Write rules by effect -----------------------------------------------------------------------
expect_allowed command "${PROBE_RELAY}/set" false
expect_denied command "${PROBE_RELAY}/state" false
expect_denied tank "${PROBE_RELAY}/set" false
expect_denied esp32 "${PROBE_RELAY}/set" false
expect_denied reader "${PROBE_RELAY}/set" false
demo_now="$(docker exec "${BROKER_CONTAINER}" mosquitto_sub -h localhost -u reader -P "${READER_PW}" -t renewvan/demo/enabled -W 2 -C 1 2>/dev/null || true)"
expect_denied demo renewvan/demo/enabled/set "${demo_now:-false}"
expect_allowed command renewvan/demo/enabled/set "${demo_now:-false}"

# --- 4. Anonymous clients are refused (only once the cutover is done) ------------------------------------
if [[ "$(get BROKER_ACL_OPEN)" == "0" ]]; then
  anon_rc=0
  docker exec "${BROKER_CONTAINER}" mosquitto_pub -h localhost -t "${PROBE_RELAY}/set" -m false >/dev/null 2>&1 || anon_rc=$?
  if [[ "${anon_rc}" == "0" ]]; then fail "an anonymous client can still connect and publish (allow_anonymous is not false)"; else pass "anonymous cannot connect (mosquitto_pub rc=${anon_rc})"; fi
else
  note "BROKER_ACL_OPEN=1: migration state, anonymous clients may still connect and write until the cutover"
fi

if [[ "${FAILED}" == "0" ]]; then echo "All broker auth checks passed."; else echo "Broker auth checks FAILED." >&2; fi
exit "${FAILED}"
