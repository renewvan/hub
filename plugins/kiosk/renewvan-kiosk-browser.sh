#!/bin/sh
# renewvan kiosk browser — the single place the kiosk Chromium command line
# lives. Run as cage's client by renewvan-cage.service at boot, and relaunched
# by bin/deploy.sh (inside the same cage session) after a dashboard bump.
#
# Waits for the dashboard nginx before exec'ing Chromium so a boot-order race
# never parks the kiosk on a connection-refused page. exec (not &) so cage
# sees Chromium as its client: when Chromium exits, cage exits and systemd
# restarts the whole session.
URL="${KIOSK_URL:-http://localhost}"

i=0
until curl -sf -o /dev/null "${URL}" || [ "${i}" -ge 60 ]; do
  i=$((i + 1))
  sleep 1
done

exec chromium \
  --kiosk \
  --ozone-platform=wayland \
  --password-store=basic \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-translate \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  --no-first-run \
  "${URL}"
