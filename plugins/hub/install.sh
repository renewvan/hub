#!/usr/bin/env bash
# plugins/hub/install.sh
# Idempotent setup for the renewvan hub status plugin. Safe to re-run.
# Run as root (called by bin/deploy.sh via ssh sudo bash).
set -euo pipefail

PLUGIN_DIR="/opt/renewvan/plugins/hub"
SERVICE_FILE="renewvan-hub.service"
ENV_FILE="/etc/renewvan/hub.env"

python3 -m venv /opt/renewvan/venvs/hub
/opt/renewvan/venvs/hub/bin/pip install --quiet -r "${PLUGIN_DIR}/requirements.txt"
echo "==> hub plugin venv ready"

# Seed the env file before the first start so the unit never reads a missing one.
if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" << 'ENV'
# renewvan hub status plugin configuration
# Uncomment and set values to override defaults.
# MQTT_HOST=localhost
# MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=
# POLL_INTERVAL=30
# PRIMARY_BATTERY_PATH=/var/lib/renewvan-hub/primary-battery.json
ENV
  echo "==> Seeded ${ENV_FILE}"
fi

cp "${PLUGIN_DIR}/${SERVICE_FILE}" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "${SERVICE_FILE%.service}"
# restart, not enable --now alone: that leaves a running service on its old script
# after a redeploy, and the script is the thing this plugin ships.
systemctl restart "${SERVICE_FILE%.service}"
echo "==> renewvan-hub service enabled and running"
