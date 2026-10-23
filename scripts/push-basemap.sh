#!/usr/bin/env bash
# Replace the dashboard's offline Basemap on a Pi, safely. Run from your own machine.
#
# Usage: scripts/push-basemap.sh <file.pmtiles> [--host <ssh-alias-or-user@host>]
#
# Order: verify locally -> check free space on the Pi (> 2x the file) -> copy to a
# temporary name beside the live file -> atomic move into place -> restart the dashboard
# -> expect HTTP 206 on a range request. Any failure leaves the previous map untouched,
# removes the temporary file and exits non-zero.
#
# Why restart: the dashboard mounts the Basemap as a single file, which pins the inode,
# so after the move the container keeps reading the old (unlinked) file until restarted.
# Why not copy in place: that exposes a growing, truncated archive to every open map.
set -euo pipefail

HOST="renewvan"
FILE=""
REMOTE_DIR="/opt/renewvan/hub"
DEFAULT_BASEMAP="/etc/renewvan/dashboard/basemap.pmtiles"
CONTAINER="renewvan-dashboard"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || die "--host needs a value"
      HOST="$2"
      shift 2
      ;;
    -*)
      die "Unknown argument: $1"
      ;;
    *)
      [[ -z "${FILE}" ]] || die "Only one file may be given (got '${FILE}' and '$1')"
      FILE="$1"
      shift
      ;;
  esac
done

[[ -n "${FILE}" ]] || die "Usage: scripts/push-basemap.sh <file.pmtiles> [--host <ssh-alias>]"
[[ -f "${FILE}" ]] || die "Not a file: ${FILE}"
command -v pmtiles >/dev/null 2>&1 || die "pmtiles CLI not found; install it (https://docs.protomaps.com/pmtiles/cli) to verify the file before it replaces a good map"

echo "==> Verifying ${FILE}"
pmtiles verify "${FILE}" || die "pmtiles verify failed: refusing to push ${FILE}"

# Portable across macOS and Linux (wc -c reads no more than the file's size).
SIZE="$(wc -c <"${FILE}" | tr -d ' ')"
[[ "${SIZE}" -gt 0 ]] || die "${FILE} is empty"

echo "==> Resolving the Basemap path on ${HOST}"
# The same key bin/deploy.sh seeds; the Pi's .env owns where the file lives. Capture ssh's own
# exit status (not a local pipe's) so an unreachable host is reported as such.
RAW="$(ssh "${HOST}" "sed -n 's|^DASHBOARD_BASEMAP_PATH=||p' ${REMOTE_DIR}/.env 2>/dev/null | tail -n 1")" \
  || die "Cannot reach ${HOST} over ssh"
# Compose strips quotes and a trailing CR from .env values; do the same.
DEST="$(printf '%s' "${RAW}" | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")"
DEST="${DEST:-${DEFAULT_BASEMAP}}"
case "${DEST}" in
  /*) ;;
  *) die "DASHBOARD_BASEMAP_PATH=${DEST} on ${HOST} is relative; set an absolute path in ${REMOTE_DIR}/.env (bin/deploy.sh does this)" ;;
esac
case "${DEST}" in
  *"'"*) die "DASHBOARD_BASEMAP_PATH=${DEST} contains a single quote; not supported" ;;
esac
TMP="${DEST}.tmp.$$"

# An older Pi may hold a root-owned *directory* here (Docker creates one for a missing
# single-file mount source). mv onto a directory succeeds by moving the file INSIDE it, which
# would strand the new map, so refuse before touching anything.
ssh "${HOST}" "[ ! -d '${DEST}' ]" || die "${DEST} on ${HOST} is a directory (Docker creates one when the mount source is missing). Remove it ('sudo rmdir' if empty) and run bin/deploy.sh to create the placeholder, then retry"

# Free space check, in bytes, on the filesystem that holds the live file. A replace
# briefly holds the old and the new file together, hence strictly more than twice.
echo "==> Checking free space on ${HOST}"
FREE="$(ssh "${HOST}" "df -Pk \"\$(dirname '${DEST}')\" | awk 'NR==2 {print \$4 * 1024}'")" \
  || die "Cannot read free space on ${HOST}"
[[ "${FREE}" =~ ^[0-9]+$ ]] || die "Unexpected free-space answer from ${HOST}: '${FREE}'"
if ! awk -v free="${FREE}" -v size="${SIZE}" 'BEGIN { exit !(free > 2 * size) }'; then
  die "Not enough free space on ${HOST}: ${FREE} bytes free, need more than $((2 * SIZE)) (twice the ${SIZE}-byte file)"
fi

cleanup() {
  # Best effort: the previous map is never touched before the final mv, so this only
  # frees the temporary copy. Ignore failures (e.g. the Pi is unreachable).
  ssh "${HOST}" "rm -f '${TMP}'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'echo "Interrupted: previous map left in place." >&2; exit 130' INT TERM

echo "==> Copying to ${HOST}:${TMP}"
scp -q "${FILE}" "${HOST}:${TMP}" || die "Copy failed"

# Compare sizes so a short write never goes live. (pmtiles itself is not on the Pi.)
REMOTE_SIZE="$(ssh "${HOST}" "wc -c < '${TMP}'" | tr -d ' ')" || die "Cannot stat the copied file"
[[ "${REMOTE_SIZE}" == "${SIZE}" ]] || die "Copied file is ${REMOTE_SIZE} bytes, expected ${SIZE}; previous map left in place"

echo "==> Moving into place"
ssh "${HOST}" "mv -f '${TMP}' '${DEST}'" || die "Move failed; previous map left in place"

echo "==> Restarting ${CONTAINER} (a single-file mount pins the old inode)"
ssh "${HOST}" "docker restart ${CONTAINER}" >/dev/null || die "Could not restart ${CONTAINER}; the new map is in place but the dashboard may still serve the old one"

echo "==> Checking the dashboard serves the new map"
# The container needs a moment to bind port 80 again; retry briefly.
CODE=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  CODE="$(ssh "${HOST}" "curl -s -o /dev/null -w '%{http_code}' -H 'Range: bytes=0-15' http://localhost/maps/basemap.pmtiles" 2>/dev/null || true)"
  [[ "${CODE}" == "206" ]] && break
  sleep 1
done
[[ "${CODE}" == "206" ]] || die "Dashboard answered HTTP ${CODE:-none} to a range request on /maps/basemap.pmtiles, expected 206"

echo "==> Basemap pushed: ${DEST} (${SIZE} bytes), dashboard serves it (206)"
