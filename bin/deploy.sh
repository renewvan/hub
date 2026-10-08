#!/usr/bin/env bash
# Repeatable deploy: rsync this repo's compose artifacts to a Pi already
# bootstrapped by bin/bootstrap-pi.sh, then `docker compose pull && up -d`
# remotely. Run from the repo root.
#
# Usage: bin/deploy.sh [--host <ssh-alias-or-user@host>]
set -euo pipefail

HOST="renewvan"
REMOTE_DIR="/opt/renewvan/hub"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "==> Syncing compose artifacts to ${HOST}:${REMOTE_DIR}"
ssh "${HOST}" "mkdir -p ${REMOTE_DIR} /opt/renewvan/plugins/kiosk /opt/renewvan/plugins/tailscale /opt/renewvan/venvs"
rsync -az docker-compose.yml "${HOST}:${REMOTE_DIR}/docker-compose.yml"
rsync -az --delete docker/ "${HOST}:${REMOTE_DIR}/docker/"
rsync -az .env.example "${HOST}:${REMOTE_DIR}/.env.example"
rsync -az --delete --exclude='.venv' plugins/kiosk/ "${HOST}:/opt/renewvan/plugins/kiosk/"
rsync -az --delete plugins/tailscale/ "${HOST}:/opt/renewvan/plugins/tailscale/"

echo "==> Ensuring remote config"
ssh "${HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${REMOTE_DIR}"

mkdir -p /etc/renewvan/mosquitto /etc/renewvan/tank /etc/renewvan/gps /etc/renewvan/temperature/state

# plugin/kiosk: create venv (Bookworm externally-managed Python) and install deps
python3 -m venv /opt/renewvan/venvs/kiosk
/opt/renewvan/venvs/kiosk/bin/pip install --quiet -r /opt/renewvan/plugins/kiosk/requirements.txt
sudo cp /opt/renewvan/plugins/kiosk/renewvan-node-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now renewvan-node-kiosk.service
# Kiosk touch input: stock Pi OS labwc rc.xml mouse-emulates the
# touchscreen (wl_pointer only) — flip to real wl_touch so touch-drag
# scrolling works in the dashboard. Idempotent; see the script's header
# for the full rationale.
sudo bash /opt/renewvan/plugins/kiosk/fix-labwc-touch-scroll.sh
if [[ ! -f /etc/renewvan/node-kiosk.env ]]; then
  printf '# renewvan kiosk plugin environment overrides\n# See plugins/kiosk/README.md for available variables.\n# Uncomment and set to override defaults:\n# MQTT_HOST=localhost\n# MQTT_PORT=1883\n# MQTT_USERNAME=\n# MQTT_PASSWORD=\n# DISPLAY_ON_CMD=vcgencmd display_power 1\n# DISPLAY_OFF_CMD=vcgencmd display_power 0\n# DISPLAY_QUERY_CMD=vcgencmd display_power\n' | sudo tee /etc/renewvan/node-kiosk.env >/dev/null
fi

# plugin/tailscale: install tailscale, venv, service
sudo bash /opt/renewvan/plugins/tailscale/install.sh

if [[ ! -f .env ]]; then
  echo "No .env found — seeding from .env.example."
  cp .env.example .env
  sed -i 's|^TANK_CONFIG_PATH=.*|TANK_CONFIG_PATH=/etc/renewvan/tank/config.ini|' .env
  sed -i 's|^GPS_CONFIG_PATH=.*|GPS_CONFIG_PATH=/etc/renewvan/gps/config.ini|' .env
  sed -i 's|^TEMPERATURE_CONFIG_PATH=.*|TEMPERATURE_CONFIG_PATH=/etc/renewvan/temperature/config.ini|' .env
  sed -i 's|^TEMPERATURE_STATE_PATH=.*|TEMPERATURE_STATE_PATH=/etc/renewvan/temperature/state|' .env
  sed -i 's|^MOSQUITTO_CONFIG_PATH=.*|MOSQUITTO_CONFIG_PATH=/etc/renewvan/mosquitto|' .env
  echo
  echo "==> Edit ${REMOTE_DIR}/.env on the Pi to fill in secrets (INFLUXDB_ADMIN_PASSWORD,"
  echo "    INFLUXDB_ADMIN_TOKEN, VICTRON_MQTT_HOST/PORTAL_ID, RENEWVAN_WS_URL), then re-run"
  echo "    bin/deploy.sh."
  exit 1
fi

# GPS_CONFIG_PATH may be missing from a .env predating node-gps's addition
# to .env.example (the branch above only seeds a brand-new .env) --
# ensure it's present either way, idempotently.
grep -q '^GPS_CONFIG_PATH=' .env || echo 'GPS_CONFIG_PATH=/etc/renewvan/gps/config.ini' >> .env
# Same for node-temperature's keys.
grep -q '^TEMPERATURE_CONFIG_PATH=' .env || echo 'TEMPERATURE_CONFIG_PATH=/etc/renewvan/temperature/config.ini' >> .env
grep -q '^TEMPERATURE_STATE_PATH=' .env || echo 'TEMPERATURE_STATE_PATH=/etc/renewvan/temperature/state' >> .env

if [[ ! -f /etc/renewvan/mosquitto/mosquitto.conf ]]; then
  echo "Seeding /etc/renewvan/mosquitto/mosquitto.conf from the repo default."
  cp docker/mosquitto/config/mosquitto.conf /etc/renewvan/mosquitto/mosquitto.conf
fi

if [[ ! -f /etc/renewvan/gps/config.ini ]]; then
  echo "Seeding /etc/renewvan/gps/config.ini from the repo default (sets [mqtt] host = mosquitto; serial_port defaults to /dev/ttyACM0 in the image)."
  cp docker/gps/config.ini.default /etc/renewvan/gps/config.ini
fi

if [[ ! -f /etc/renewvan/temperature/config.ini ]]; then
  echo "Seeding /etc/renewvan/temperature/config.ini from the repo default (sets [mqtt] host = mosquitto and state_dir; map probes to ids/names there)."
  cp docker/temperature/config.ini.default /etc/renewvan/temperature/config.ini
fi

if [[ ! -f /etc/renewvan/tank/config.ini ]]; then
  echo
  echo "==> /etc/renewvan/tank/config.ini is missing (tank calibration — see"
  echo "    docs/porting-dbus-to-mqtt-node.md). Place it on the Pi, then re-run"
  echo "    bin/deploy.sh."
  exit 1
fi

# node-gps is behind the \`gps\` compose profile because its \`devices:\` entry
# makes Docker refuse to create the container (and compose abort the whole
# \`up -d\`) when /dev/ttyACM0 does not exist. Enable the profile only when the
# module is plugged in; re-running deploy after plugging it in starts the node.
# Without the module, any previously created gps container is stopped so it
# doesn't crash-loop on the missing device.
if [[ -e /dev/ttyACM0 ]]; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}gps"
  echo "==> GPS device /dev/ttyACM0 present: enabling the gps profile"
else
  echo "==> No /dev/ttyACM0: skipping node-gps (plug in the GPS module and re-run deploy to enable it)"
  docker compose rm -sf node-gps >/dev/null 2>&1 || true
fi

echo "==> docker compose pull && up -d"
docker compose pull
docker compose up -d
REMOTE

echo "==> Reloading kiosk browser"
ssh "${HOST}" "
  pkill chromium || true
  # Fresh cache: chromium heuristic-caches index.html, which would keep
  # serving the previous dashboard build after an image bump.
  rm -rf "\${HOME}/.cache/chromium" || true
  sleep 2
  WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 nohup chromium \
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
    http://localhost >/dev/null 2>&1 &
"

echo "==> Deploy complete"
