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
ssh "${HOST}" "mkdir -p ${REMOTE_DIR} /opt/renewvan/node-kiosk"
rsync -az docker-compose.yml "${HOST}:${REMOTE_DIR}/docker-compose.yml"
rsync -az --delete docker/ "${HOST}:${REMOTE_DIR}/docker/"
rsync -az .env.example "${HOST}:${REMOTE_DIR}/.env.example"
rsync -az --delete node-kiosk/ "${HOST}:/opt/renewvan/node-kiosk/"

echo "==> Ensuring remote config"
ssh "${HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${REMOTE_DIR}"

mkdir -p /etc/renewvan/mosquitto /etc/renewvan/tank

# wlopm: Wayland output power manager (display sleep/wake on Bookworm KMS stack)
if ! command -v wlopm >/dev/null 2>&1; then
  echo "==> Installing wlopm"
  sudo apt-get install -y -qq wlopm
fi

# node-kiosk: create venv (Bookworm externally-managed Python) and install deps
python3 -m venv /opt/renewvan/node-kiosk/.venv
/opt/renewvan/node-kiosk/.venv/bin/pip install --quiet -r /opt/renewvan/node-kiosk/requirements.txt
sudo cp /opt/renewvan/node-kiosk/renewvan-node-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now renewvan-node-kiosk.service
if [[ ! -f /etc/renewvan/node-kiosk.env ]]; then
  printf '# renewvan node-kiosk environment overrides\n# See node-kiosk/README.md for available variables.\n# Uncomment and set to override defaults:\n# MQTT_HOST=localhost\n# MQTT_PORT=1883\n# MQTT_USERNAME=\n# MQTT_PASSWORD=\n# DISPLAY_ON_CMD=vcgencmd display_power 1\n# DISPLAY_OFF_CMD=vcgencmd display_power 0\n# DISPLAY_QUERY_CMD=vcgencmd display_power\n' | sudo tee /etc/renewvan/node-kiosk.env >/dev/null
fi

if [[ ! -f .env ]]; then
  echo "No .env found — seeding from .env.example."
  cp .env.example .env
  sed -i 's|^TANK_CONFIG_PATH=.*|TANK_CONFIG_PATH=/etc/renewvan/tank/config.ini|' .env
  sed -i 's|^MOSQUITTO_CONFIG_PATH=.*|MOSQUITTO_CONFIG_PATH=/etc/renewvan/mosquitto|' .env
  echo
  echo "==> Edit ${REMOTE_DIR}/.env on the Pi to fill in secrets (INFLUXDB_ADMIN_PASSWORD,"
  echo "    INFLUXDB_ADMIN_TOKEN, VICTRON_MQTT_HOST/PORTAL_ID, RENEWVAN_WS_URL), then re-run"
  echo "    bin/deploy.sh."
  exit 1
fi

if [[ ! -f /etc/renewvan/mosquitto/mosquitto.conf ]]; then
  echo "Seeding /etc/renewvan/mosquitto/mosquitto.conf from the repo default."
  cp docker/mosquitto/config/mosquitto.conf /etc/renewvan/mosquitto/mosquitto.conf
fi

if [[ ! -f /etc/renewvan/tank/config.ini ]]; then
  echo
  echo "==> /etc/renewvan/tank/config.ini is missing (tank calibration — see"
  echo "    docs/porting-dbus-to-mqtt-node.md). Place it on the Pi, then re-run"
  echo "    bin/deploy.sh."
  exit 1
fi

echo "==> docker compose pull && up -d"
docker compose pull
docker compose up -d
REMOTE

echo "==> Deploy complete"
