#!/usr/bin/env bash
# Runs ON the Pi (copied and run over ssh by bin/bootstrap-pi.sh and bin/bootstrap-kiosk-pi.sh).
# Idempotent. Creates the unprivileged system user the kiosk browser and the display bridge run as,
# and strips it of any admin group. Decided in
# .scratch/security-audit/issues/04-kiosk-privilege-split.md:
#
#   renewvan-kiosk   locked password, no login shell, groups video/input/render only,
#                    never sudo or docker (docker is root-equivalent).
#
# Chromium needs a writable profile directory, so the user owns a small home; it keeps no state
# worth keeping (bin/deploy.sh clears the cache on every deploy).
set -euo pipefail

KIOSK_USER="renewvan-kiosk"
KIOSK_HOME="/var/lib/renewvan-kiosk"

if ! id "${KIOSK_USER}" >/dev/null 2>&1; then
  echo "==> Creating system user ${KIOSK_USER}"
  sudo useradd --system --user-group --home-dir "${KIOSK_HOME}" --create-home \
    --shell /usr/sbin/nologin "${KIOSK_USER}"
fi
sudo passwd -l "${KIOSK_USER}" >/dev/null 2>&1 || true

for g in video input render; do
  if getent group "${g}" >/dev/null; then
    sudo usermod -aG "${g}" "${KIOSK_USER}"
  fi
done
for g in sudo docker adm; do
  if id -nG "${KIOSK_USER}" | tr ' ' '\n' | grep -qx "${g}"; then
    sudo gpasswd -d "${KIOSK_USER}" "${g}" >/dev/null
  fi
done

sudo install -d -o "${KIOSK_USER}" -g "${KIOSK_USER}" -m 700 "${KIOSK_HOME}"
echo "==> ${KIOSK_USER} ready (groups: $(id -nG "${KIOSK_USER}"))"
