#!/usr/bin/env bash
# Run BY HAND on the Pi (ssh in, then bin/check-hardening.sh, or `ssh <pi> 'bash -s' < bin/check-hardening.sh`)
# after any deploy that touched compose, the bootstrap scripts or plugin permissions. Not called by
# deploy.sh and not scheduled. Decided in .scratch/security-audit/issues/07-verifying-the-fixes.md.
#
#   kiosk   renewvan-kiosk is not in sudo/docker and cannot sudo; Chromium and the display bridge run
#           as it; /opt/renewvan/plugins and /opt/renewvan/venvs are root-owned, not group/world-writable.
#   drivers wattcycle and vedirect containers run as a non-root uid; the host D-Bus policy exists;
#           vedirect's /dev is read-only; each driver still reaches BlueZ / the serial port.
#
# The static compose checks run in CI (bin/test_compose_hardening.py); this is the runtime half.
# Exit 0 = all passed (skips are not failures), 1 = at least one check failed.
# No -e on purpose: every check runs and failures accumulate into the exit code.
set -uo pipefail

KIOSK_USER="renewvan-kiosk"
FAILED=0
pass() { printf 'ok    %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; FAILED=1; }
skip() { printf 'skip  %s\n' "$*"; }

running() { docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$1"; }

echo "== kiosk user =="
if ! id "${KIOSK_USER}" >/dev/null 2>&1; then
  fail "user ${KIOSK_USER} does not exist (run bin/bootstrap-kiosk-pi.sh)"
else
  groups="$(id -nG "${KIOSK_USER}")"
  for g in sudo docker; do
    if tr ' ' '\n' <<<"${groups}" | grep -qx "${g}"; then
      fail "${KIOSK_USER} is in group ${g}"
    else
      pass "${KIOSK_USER} is not in group ${g}"
    fi
  done
  if ! sudo -n true >/dev/null 2>&1; then
    skip "cannot run the sudo -n check as ${KIOSK_USER}: this account has no passwordless sudo"
  elif sudo -n -u "${KIOSK_USER}" sudo -n true >/dev/null 2>&1; then
    fail "${KIOSK_USER} can run sudo without a password"
  else
    pass "sudo -n true fails for ${KIOSK_USER}"
  fi
  chromium_user="$(ps -C chromium -o user= 2>/dev/null | sort -u | tr '\n' ' ')"
  if [[ -z "${chromium_user// /}" ]]; then
    skip "Chromium is not running (kiosk session not started)"
  elif [[ "${chromium_user// /}" == "${KIOSK_USER}" ]]; then
    pass "Chromium runs as ${KIOSK_USER}"
  else
    fail "Chromium runs as: ${chromium_user}"
  fi
  bridge_user="$(ps -eo user:32=,args= | awk '/kiosk\.py/ && !/awk/ {print $1; exit}')"
  if [[ -z "${bridge_user}" ]]; then
    fail "kiosk.py (display bridge) is not running"
  elif [[ "${bridge_user}" == "${KIOSK_USER}" ]]; then
    pass "kiosk.py runs as ${KIOSK_USER}"
  else
    fail "kiosk.py runs as ${bridge_user}"
  fi
fi

echo "== plugin and venv ownership =="
for dir in /opt/renewvan/plugins /opt/renewvan/venvs; do
  if [[ ! -d "${dir}" ]]; then
    fail "${dir} is missing"
    continue
  fi
  owner="$(stat -c '%U:%G' "${dir}")"
  if [[ "${owner}" == "root:root" ]]; then pass "${dir} is root:root"; else fail "${dir} is ${owner}"; fi
  loose="$(find "${dir}" \( -perm -g+w -o -perm -o+w \) ! -type l 2>/dev/null | head -3)"
  if [[ -z "${loose}" ]]; then
    pass "nothing under ${dir} is group/world-writable"
  else
    fail "group/world-writable under ${dir}: $(tr '\n' ' ' <<<"${loose}")"
  fi
  notroot="$(find "${dir}" ! -user root 2>/dev/null | head -3)"
  if [[ -z "${notroot}" ]]; then pass "everything under ${dir} is owned by root"; else fail "not root-owned: $(tr '\n' ' ' <<<"${notroot}")"; fi
done

echo "== driver containers =="
for c in renewvan-wattcycle renewvan-vedirect; do
  if ! running "${c}"; then
    skip "${c} is not running (no battery MAC / no VE.Direct cable)"
    continue
  fi
  uid="$(docker exec "${c}" id -u 2>/dev/null)"
  if [[ -n "${uid}" && "${uid}" != "0" ]]; then pass "${c} runs as uid ${uid}"; else fail "${c} runs as uid ${uid:-?}"; fi
  caps="$(docker inspect -f '{{.HostConfig.CapDrop}}' "${c}")"
  if [[ "${caps}" == *ALL* ]]; then pass "${c} drops all capabilities"; else fail "${c} CapDrop is ${caps}"; fi
  if [[ "$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "${c}")" == *no-new-privileges* ]]; then
    pass "${c} has no-new-privileges"
  else
    fail "${c} lacks no-new-privileges"
  fi
done

if id -u renewvan-wattcycle >/dev/null 2>&1; then
  pass "host account renewvan-wattcycle exists (dbus-daemon needs it for the container uid)"
else
  fail "host account renewvan-wattcycle is missing (run bin/bootstrap-pi.sh)"
fi

if [[ -f /etc/dbus-1/system.d/renewvan-wattcycle.conf ]]; then
  pass "D-Bus policy /etc/dbus-1/system.d/renewvan-wattcycle.conf is installed"
else
  fail "D-Bus policy /etc/dbus-1/system.d/renewvan-wattcycle.conf is missing (run bin/bootstrap-pi.sh)"
fi

if running renewvan-wattcycle; then
  if docker exec renewvan-wattcycle python3 -c '
import socket
s = socket.socket(socket.AF_UNIX); s.connect("/var/run/dbus/system_bus_socket")' >/dev/null 2>&1; then
    pass "wattcycle reaches the system D-Bus socket"
  else
    fail "wattcycle cannot connect to /var/run/dbus/system_bus_socket"
  fi
  if docker logs --since 2m renewvan-wattcycle 2>&1 | grep -qiE "AccessDenied|not allowed to send"; then
    fail "wattcycle logs D-Bus access errors: the policy may be too narrow for BlueZ"
  else
    pass "no D-Bus denials in the last 2 minutes of wattcycle logs"
  fi
fi

if running renewvan-vedirect; then
  if docker inspect -f '{{range .Mounts}}{{if eq .Destination "/dev"}}{{.RW}}{{end}}{{end}}' renewvan-vedirect | grep -qx false; then
    pass "vedirect /dev is mounted read-only"
  else
    fail "vedirect /dev is writable"
  fi
  if docker exec renewvan-vedirect sh -c 'ls /dev/serial/by-id/* >/dev/null 2>&1 && for f in /dev/serial/by-id/*; do [ -r "$f" ] && [ -w "$f" ] && exit 0; done; exit 1'; then
    pass "vedirect can open a serial port"
  else
    fail "vedirect cannot read/write any /dev/serial/by-id port"
  fi
fi

if [[ "${FAILED}" == "0" ]]; then
  echo "All hardening checks passed."
else
  echo "Hardening checks FAILED." >&2
fi
exit "${FAILED}"
