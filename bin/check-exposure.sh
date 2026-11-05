#!/usr/bin/env bash
# Run from a SECOND host on the van LAN (a laptop on the van Wi-Fi), not on the Pi.
# Verifies docs/adr/0024-history-reaches-browsers-through-a-caddy-influx-proxy.md:
#
#   1. InfluxDB (8086) and Grafana (3000) refuse connections on the Pi's LAN address.
#   2. Caddy's /influx answers a query with no credentials (it injects the read-only token).
#   3. A write through /influx is rejected (only POST /api/v2/query is forwarded).
#
# Usage: bin/check-exposure.sh <pi-lan-ip-or-name>
# Needs: nc, curl. Exit 0 = every check passed, 1 = at least one failed, 2 = usage error.
set -uo pipefail

HOST="${1:-}"
if [[ -z "${HOST}" || "${HOST}" == -* ]]; then
  echo "usage: $0 <pi-lan-ip-or-name>" >&2
  exit 2
fi
for tool in nc curl; do
  command -v "${tool}" >/dev/null 2>&1 || { echo "ERROR: ${tool} is required" >&2; exit 2; }
done

FAILED=0
pass() { printf 'ok    %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; FAILED=1; }

for port in 8086 3000; do
  if nc -z -w 3 "${HOST}" "${port}" >/dev/null 2>&1; then
    fail "${HOST}:${port} accepts connections (must be loopback-only on the Pi)"
  else
    pass "${HOST}:${port} refuses connections"
  fi
done

# Caddy uses a local CA (`tls internal`), so this host may not trust it: -k only skips that check.
BASE="https://${HOST}/influx"

code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/v2/query?org=renewvan" \
  -H 'Content-Type: application/json' \
  -d '{"query":"buckets()","type":"flux"}' || true)"
if [[ "${code}" == "200" ]]; then
  pass "POST /influx/api/v2/query answers without credentials (200)"
else
  fail "POST /influx/api/v2/query returned ${code:-no response}, expected 200"
fi

code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' -X POST \
  "${BASE}/api/v2/write?org=renewvan&bucket=renewvan" \
  -H 'Authorization: Token not-a-real-token' \
  --data-binary 'exposure_check value=1' || true)"
case "${code}" in
  204 | 200 | "" | 000) fail "a write through /influx was not rejected (${code:-no response})" ;;
  *) pass "write through /influx is rejected (${code})" ;;
esac

code="$(curl -sk -m 15 -o /dev/null -w '%{http_code}' "${BASE}/api/v2/buckets" || true)"
case "${code}" in
  200 | "" | 000) fail "GET /influx/api/v2/buckets was not rejected (${code:-no response})" ;;
  *) pass "other InfluxDB paths are rejected (${code})" ;;
esac

if [[ "${FAILED}" == "0" ]]; then
  echo "All exposure checks passed."
else
  echo "Exposure checks FAILED." >&2
fi
exit "${FAILED}"
