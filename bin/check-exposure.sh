#!/usr/bin/env bash
# Run from a SECOND host on the van LAN (a laptop on the van Wi-Fi), not on the Pi.
# Verifies docs/adr/0024-history-reaches-browsers-through-a-caddy-influx-proxy.md:
#
#   1. InfluxDB (8086) and Grafana (3000) refuse connections on the Pi's LAN address.
#   2. Caddy's basic_auth gate (ADR 0025): /influx and /mqtt answer 401 with no login. With the login
#      (CHECK_EXPOSURE_PASSWORD, user renewvan) /influx answers a query (Caddy injects the read-only token).
#   3. A write through /influx is rejected (only POST /api/v2/query is forwarded).
#
#   4. (--published, run ON the Pi) Caddy's 443 and 80 are published on specific addresses only, never
#      0.0.0.0 / [::] (docs/adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md).
#      From a second host a wildcard publish is indistinguishable from the LAN bind, so this one
#      reads the container's port mappings (CONTAINER_CLI, default docker).
#
# Usage: [CHECK_EXPOSURE_PASSWORD=<caddy login>] bin/check-exposure.sh <pi-lan-ip-or-name>
#        bin/check-exposure.sh --published      (on the Pi; only check 4)
# Needs: nc, curl. Exit 0 = every check passed, 1 = at least one failed, 2 = usage error.
# No -e on purpose: every check runs and failures accumulate into the exit code.
set -uo pipefail

FAILED=0
pass() { printf 'ok    %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; FAILED=1; }

# Fails on any all-interface publish of 443 or 80 (80 is only Caddy's redirect to https); reads
# `docker port` lines like `443/tcp -> 10.0.0.5:443`.
check_published() {
  local cli="${CONTAINER_CLI:-docker}" out port
  for port in 443 80; do
    if ! out="$("${cli}" port renewvan-caddy "${port}/tcp" 2>&1)"; then
      fail "cannot read renewvan-caddy's published ports (${cli} port: ${out})"
    elif [[ -z "${out}" ]]; then
      fail "renewvan-caddy publishes nothing on ${port}"
    elif printf '%s\n' "${out}" | grep -Eq '(^|-> )(0\.0\.0\.0|\[::\]|::):'; then
      fail "Caddy ${port} is published on every interface: ${out//$'\n'/, }"
    else
      pass "Caddy ${port} is published on specific addresses only: ${out//$'\n'/, }"
    fi
  done
}

if [[ "${1:-}" == "--published" ]]; then
  check_published
  exit "${FAILED}"
fi

HOST="${1:-}"
if [[ -z "${HOST}" || "${HOST}" == -* ]]; then
  echo "usage: $0 <pi-lan-ip-or-name>" >&2
  exit 2
fi
for tool in nc curl; do
  command -v "${tool}" >/dev/null 2>&1 || { echo "ERROR: ${tool} is required" >&2; exit 2; }
done

for port in 8086 3000; do
  if nc -z -w 3 "${HOST}" "${port}" >/dev/null 2>&1; then
    fail "${HOST}:${port} accepts connections (must be loopback-only on the Pi)"
  else
    pass "${HOST}:${port} refuses connections"
  fi
done

# Caddy uses a local CA (`tls internal`), so this host may not trust it: -k only skips that check.
BASE="https://${HOST}/influx"

QUERY_BODY='{"query":"buckets()","type":"flux"}'

code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/v2/query?org=renewvan" \
  -H 'Content-Type: application/json' -d "${QUERY_BODY}" || true)"
if [[ "${code}" == "401" ]]; then
  pass "POST /influx/api/v2/query without the Caddy login is refused (401)"
else
  fail "POST /influx/api/v2/query without a login returned ${code:-no response}, expected 401"
fi

# A WebSocket upgrade to /mqtt without the login must be refused before the upgrade.
code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' "https://${HOST}/mqtt" \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Protocol: mqtt' || true)"
if [[ "${code}" == "401" ]]; then
  pass "/mqtt without the Caddy login is refused (401)"
else
  fail "/mqtt without a login returned ${code:-no response}, expected 401"
fi

if [[ -n "${CHECK_EXPOSURE_PASSWORD:-}" ]]; then
  code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/v2/query?org=renewvan" \
    -u "renewvan:${CHECK_EXPOSURE_PASSWORD}" -H 'Content-Type: application/json' -d "${QUERY_BODY}" || true)"
  if [[ "${code}" == "200" ]]; then
    pass "POST /influx/api/v2/query with the Caddy login answers (200)"
  else
    fail "POST /influx/api/v2/query with the login returned ${code:-no response}, expected 200"
  fi
else
  echo "skip  the with-login query (set CHECK_EXPOSURE_PASSWORD to the Caddy login password)"
fi

if [[ -n "${CHECK_EXPOSURE_PASSWORD:-}" ]]; then
  # Past the gate (with the login) a write and other paths must still be refused by the proxy itself. Without
  # the login these two only prove the gate answers 401, which is weaker but still a refusal.
  AUTH=(-H 'Authorization: Token not-a-real-token')
  if [[ -n "${CHECK_EXPOSURE_PASSWORD:-}" ]]; then AUTH=(-u "renewvan:${CHECK_EXPOSURE_PASSWORD}"); fi

  code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' -X POST \
    "${BASE}/api/v2/write?org=renewvan&bucket=renewvan" \
    "${AUTH[@]}" \
    --data-binary 'exposure_check value=1' || true)"
  case "${code}" in
    204 | 200 | "" | 000) fail "a write through /influx was not rejected (${code:-no response})" ;;
    *) pass "write through /influx is rejected (${code})" ;;
  esac

  code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' "${AUTH[@]}" "${BASE}/api/v2/buckets" || true)"
  case "${code}" in
    200 | "" | 000) fail "GET /influx/api/v2/buckets was not rejected (${code:-no response})" ;;
    *) pass "other InfluxDB paths are rejected (${code})" ;;
  esac
else
  echo "skip  the write and other-path refusals (without the login they only meet the 401 gate; set CHECK_EXPOSURE_PASSWORD)"
fi

if [[ "${FAILED}" == "0" ]]; then
  echo "Also run 'bin/check-exposure.sh --published' on the Pi (Caddy 443 must not bind every interface)."
  echo "All exposure checks passed."
else
  echo "Exposure checks FAILED." >&2
fi
exit "${FAILED}"
