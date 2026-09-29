#!/usr/bin/env bash
# plugins/tailscale/install.sh
# Idempotent setup for the renewvan Tailscale plugin. Safe to re-run.
# Run as root (called by bin/deploy.sh via ssh sudo bash).
set -euo pipefail

PLUGIN_DIR="/opt/renewvan/plugins/tailscale"
SERVICE_FILE="renewvan-tailscale.service"
ENV_FILE="/etc/renewvan/tailscale.env"

# ---------------------------------------------------------------------------
# 1. Install tailscale if missing
# ---------------------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  echo "==> Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "==> Tailscale already installed ($(tailscale version | head -1))"
fi

# ---------------------------------------------------------------------------
# 2. Enable tailscaled daemon
# ---------------------------------------------------------------------------
systemctl enable --now tailscaled
echo "==> tailscaled enabled and running"

# ---------------------------------------------------------------------------
# 3. Python venv + deps
# ---------------------------------------------------------------------------
python3 -m venv /opt/renewvan/venvs/tailscale
/opt/renewvan/venvs/tailscale/bin/pip install --quiet -r "${PLUGIN_DIR}/requirements.txt"
echo "==> Python venv ready"

# ---------------------------------------------------------------------------
# 4. Systemd service
# ---------------------------------------------------------------------------
cp "${PLUGIN_DIR}/${SERVICE_FILE}" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now "${SERVICE_FILE%.service}"
echo "==> renewvan-tailscale service enabled and running"

# ---------------------------------------------------------------------------
# 5. Seed env file if missing
# ---------------------------------------------------------------------------
if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" << 'ENV'
# renewvan Tailscale plugin configuration
# Uncomment and set values to override defaults.
# MQTT_HOST=localhost
# MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=
# POLL_INTERVAL=30
ENV
  echo "==> Seeded ${ENV_FILE}"
fi

# ---------------------------------------------------------------------------
# 6. Auth check — prompt if not yet authenticated
# ---------------------------------------------------------------------------
if tailscale status >/dev/null 2>&1; then
  IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
  echo "==> Tailscale authenticated — IP: ${IP}"
else
  echo ""
  echo "  Tailscale is installed but not yet authenticated."
  echo "  Run the following on the Pi to connect to your tailnet:"
  echo ""
  echo "    sudo tailscale up"
  echo ""
  echo "  The MQTT bridge will publish disabled state until authenticated."
fi
