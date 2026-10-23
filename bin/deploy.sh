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
ssh "${HOST}" "mkdir -p ${REMOTE_DIR} /opt/renewvan/plugins/kiosk /opt/renewvan/plugins/tailscale /opt/renewvan/plugins/hub /opt/renewvan/venvs"
rsync -az docker-compose.yml "${HOST}:${REMOTE_DIR}/docker-compose.yml"
rsync -az --delete docker/ "${HOST}:${REMOTE_DIR}/docker/"
rsync -az .env.example "${HOST}:${REMOTE_DIR}/.env.example"
rsync -az --delete --exclude='.venv' plugins/kiosk/ "${HOST}:/opt/renewvan/plugins/kiosk/"
rsync -az --delete plugins/tailscale/ "${HOST}:/opt/renewvan/plugins/tailscale/"
rsync -az --delete plugins/hub/ "${HOST}:/opt/renewvan/plugins/hub/"
# The hub's version is a property of this checkout, not of the files rsynced: the Pi has no .git.
# Stamp it after the rsync above (which would otherwise --delete it) so renewvan-hub publishes it
# on renewvan/hub/status and Settings > General > Firmware can show it. Same string the ESP32
# flash script derives; an untagged tree gives a commit hash, never a refusal.
HUB_VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo dev)"
echo "==> Stamping hub version ${HUB_VERSION}"
ssh "${HOST}" "printf '%s\n' '${HUB_VERSION}' > /opt/renewvan/plugins/hub/VERSION"

echo "==> Ensuring remote config"
ssh "${HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${REMOTE_DIR}"

mkdir -p /etc/renewvan/mosquitto /etc/renewvan/tank /etc/renewvan/gps /etc/renewvan/temperature/state /etc/renewvan/tilt/state /etc/renewvan/dashboard

# plugin/kiosk: create venv (Bookworm externally-managed Python) and install deps
python3 -m venv /opt/renewvan/venvs/kiosk
/opt/renewvan/venvs/kiosk/bin/pip install --quiet -r /opt/renewvan/plugins/kiosk/requirements.txt
sudo cp /opt/renewvan/plugins/kiosk/renewvan-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now renewvan-kiosk.service
# The cage kiosk session unit (renewvan-cage.service) is installed by
# bin/bootstrap-kiosk-pi.sh, not here: it needs the kiosk user substituted in.
if [[ ! -f /etc/renewvan/kiosk.env ]]; then
  printf '# renewvan kiosk plugin environment overrides\n# See plugins/kiosk/README.md for available variables.\n# Display power defaults to the backlight bl_power device; set all three\n# DISPLAY_*_CMD to override. Uncomment and set to override defaults:\n# MQTT_HOST=localhost\n# MQTT_PORT=1883\n# MQTT_USERNAME=\n# MQTT_PASSWORD=\n' | sudo tee /etc/renewvan/kiosk.env >/dev/null
fi

# plugin/tailscale: install tailscale, venv, service
sudo bash /opt/renewvan/plugins/tailscale/install.sh

# plugin/hub: publish the Pi's own local IP to renewvan/hub/status
sudo bash /opt/renewvan/plugins/hub/install.sh

if [[ ! -f .env ]]; then
  echo "No .env found — seeding from .env.example."
  cp .env.example .env
  sed -i 's|^TANK_CONFIG_PATH=.*|TANK_CONFIG_PATH=/etc/renewvan/tank/config.ini|' .env
  sed -i 's|^GPS_CONFIG_PATH=.*|GPS_CONFIG_PATH=/etc/renewvan/gps/config.ini|' .env
  sed -i 's|^TEMPERATURE_CONFIG_PATH=.*|TEMPERATURE_CONFIG_PATH=/etc/renewvan/temperature/config.ini|' .env
  sed -i 's|^TEMPERATURE_STATE_PATH=.*|TEMPERATURE_STATE_PATH=/etc/renewvan/temperature/state|' .env
  sed -i 's|^TILT_CONFIG_PATH=.*|TILT_CONFIG_PATH=/etc/renewvan/tilt/config.ini|' .env
  sed -i 's|^TILT_STATE_PATH=.*|TILT_STATE_PATH=/etc/renewvan/tilt/state|' .env
  sed -i 's|^DASHBOARD_BASEMAP_PATH=.*|DASHBOARD_BASEMAP_PATH=/etc/renewvan/dashboard/basemap.pmtiles|' .env
  sed -i 's|^MOSQUITTO_CONFIG_PATH=.*|MOSQUITTO_CONFIG_PATH=/etc/renewvan/mosquitto|' .env
  echo
  echo "==> Edit ${REMOTE_DIR}/.env on the Pi to fill in secrets (INFLUXDB_ADMIN_PASSWORD,"
  echo "    INFLUXDB_ADMIN_TOKEN, VICTRON_MQTT_HOST/PORTAL_ID, RENEWVAN_WS_URL), then re-run"
  echo "    bin/deploy.sh."
  exit 1
fi

# A full-URL VITE_MQTT_WS_URL is opened by the *browser* (kiosk, phone), so it must
# name an address this Pi actually has right now. Moving the Pi to another router
# changes its DHCP IP and leaves the old one here: the dashboard then shows "Hub
# connection lost" with no data while every container is healthy. Fail loudly.
# The path form (/mqtt) names no host at all -- the dashboard resolves it against
# the page's own origin and its nginx proxies it to the broker -- so it cannot go
# stale and is not checked.
ws_url="\$(sed -n 's|^VITE_MQTT_WS_URL=||p' .env)"
if [[ -z "\${ws_url}" ]]; then
  echo "ERROR: VITE_MQTT_WS_URL in ${REMOTE_DIR}/.env is missing (use /mqtt or ws://<host>:9001)" >&2
  exit 1
fi
if [[ "\${ws_url}" != /* ]]; then
  ws_host="\$(printf '%s' "\${ws_url}" | sed -n 's|^wss\\{0,1\\}://\\([^:/]*\\).*|\\1|p')"
  if [[ -z "\${ws_host}" ]]; then
    echo "ERROR: VITE_MQTT_WS_URL=\${ws_url} is not /mqtt or ws://<host>:9001" >&2
    exit 1
  fi
  if ! { hostname -I | tr ' ' '\n'; hostname; hostname -f; echo localhost; } | grep -qxF "\${ws_host}"; then
    echo "ERROR: VITE_MQTT_WS_URL points at \${ws_host}, which is not an address of this Pi (\$(hostname -I | xargs))." >&2
    echo "       The Pi's IP probably changed (new router). Use VITE_MQTT_WS_URL=/mqtt, or set ws://<current-ip>:9001 in ${REMOTE_DIR}/.env, and re-run." >&2
    exit 1
  fi
fi

# GPS_CONFIG_PATH may be missing from a .env predating driver-gps's addition
# to .env.example (the branch above only seeds a brand-new .env) --
# ensure it's present either way, idempotently.
grep -q '^GPS_CONFIG_PATH=' .env || echo 'GPS_CONFIG_PATH=/etc/renewvan/gps/config.ini' >> .env
# Same for driver-temperature's keys.
grep -q '^TEMPERATURE_CONFIG_PATH=' .env || echo 'TEMPERATURE_CONFIG_PATH=/etc/renewvan/temperature/config.ini' >> .env
grep -q '^TEMPERATURE_STATE_PATH=' .env || echo 'TEMPERATURE_STATE_PATH=/etc/renewvan/temperature/state' >> .env
# Same for driver-tilt's keys.
grep -q '^TILT_CONFIG_PATH=' .env || echo 'TILT_CONFIG_PATH=/etc/renewvan/tilt/config.ini' >> .env
grep -q '^TILT_STATE_PATH=' .env || echo 'TILT_STATE_PATH=/etc/renewvan/tilt/state' >> .env
# Same for the dashboard's Basemap path.
grep -q '^DASHBOARD_BASEMAP_PATH=' .env || echo 'DASHBOARD_BASEMAP_PATH=/etc/renewvan/dashboard/basemap.pmtiles' >> .env

# The Basemap is bind-mounted as a single file. If the source is missing Docker creates a
# root-owned *directory* there and nginx answers 301 instead of serving the file. Create an
# empty placeholder (the Map page's "no map" state) only when nothing is there, so a
# redeploy never replaces a real Basemap. -e, not -f: never touch whatever already exists.
basemap_path="\$(sed -n 's|^DASHBOARD_BASEMAP_PATH=||p' .env)"
if [[ -z "\${basemap_path}" ]]; then
  echo "ERROR: DASHBOARD_BASEMAP_PATH in ${REMOTE_DIR}/.env is empty (default: /etc/renewvan/dashboard/basemap.pmtiles)" >&2
  exit 1
fi
mkdir -p "\$(dirname "\${basemap_path}")"
# A Pi that ran the dashboard before this mount existed may hold the directory Docker invents
# for a missing single-file source. Remove it only if empty; never delete content.
if [[ -d "\${basemap_path}" ]]; then
  if rmdir "\${basemap_path}" 2>/dev/null || sudo rmdir "\${basemap_path}" 2>/dev/null; then
    echo "Removed the empty directory Docker created at \${basemap_path}."
  else
    echo "ERROR: \${basemap_path} is a non-empty directory, not a Basemap file. Move its contents away and re-run." >&2
    exit 1
  fi
fi
if [[ ! -e "\${basemap_path}" ]]; then
  echo "Creating empty Basemap placeholder \${basemap_path} (push a real one with scripts/push-basemap.sh)."
  : > "\${basemap_path}"
fi

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

if [[ ! -f /etc/renewvan/tilt/config.ini ]]; then
  echo "Seeding /etc/renewvan/tilt/config.ini from the repo default (sets [mqtt] host = mosquitto, state_dir, and the face-down axis mapping; edit axis_* to match how the GY-521 is mounted)."
  cp docker/tilt/config.ini.default /etc/renewvan/tilt/config.ini
fi

if [[ ! -f /etc/renewvan/tank/config.ini ]]; then
  echo "Seeding /etc/renewvan/tank/config.ini from the repo default (sets [mqtt] host = mosquitto; the image's defaults give two 70 L tanks on ADS1115 channels 0 and 1 until you calibrate sensor_min/max)."
  cp docker/tank/config.ini.default /etc/renewvan/tank/config.ini
fi

# driver-gps is behind the \`gps\` compose profile because its \`devices:\` entry
# makes Docker refuse to create the container (and compose abort the whole
# \`up -d\`) when /dev/ttyACM0 does not exist. Enable the profile only when the
# module is plugged in; re-running deploy after plugging it in starts the device.
# Without the module, any previously created gps container is stopped so it
# doesn't crash-loop on the missing device.
if [[ -e /dev/ttyACM0 ]]; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}gps"
  echo "==> GPS device /dev/ttyACM0 present: enabling the gps profile"
else
  echo "==> No /dev/ttyACM0: skipping driver-gps (plug in the GPS module and re-run deploy to enable it)"
  docker compose rm -sf driver-gps >/dev/null 2>&1 || true
fi

# driver-venus is behind the \`venus\` compose profile: it needs VENUS_MQTT_HOST and
# VENUS_PORTAL_ID (a hard lookup, no default), so on a van with no Venus OS device
# it would crash-loop. Enable it only when .env names a Venus device; any
# previously created container is stopped otherwise.
if grep -Eq '^VENUS_MQTT_HOST=.+' .env; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}venus"
  echo "==> VENUS_MQTT_HOST set: enabling the venus profile"
else
  echo "==> No VENUS_MQTT_HOST in .env: skipping driver-venus (set it and re-run deploy on a van with Venus OS)"
  docker compose rm -sf driver-venus >/dev/null 2>&1 || true
fi

echo "==> docker compose pull && up -d"
docker compose pull
# --remove-orphans: containers have fixed container_name values, so a renamed service key leaves
# the old container holding the name and a plain up -d aborts half-way (ADR 0014's rename did).
docker compose up -d --remove-orphans
REMOTE

echo "==> Reloading kiosk browser"
# Fresh cache: chromium heuristic-caches index.html, which would keep serving
# the previous dashboard build after an image bump. The cache lives in the
# kiosk user's home, which is the ssh user's home on a single-user kiosk Pi.
# Restarting renewvan-cage relaunches cage and therefore Chromium; if the
# unit isn't installed yet (Pi not bootstrapped with bin/bootstrap-kiosk-pi.sh)
# say so instead of silently leaving the old dashboard on screen.
ssh "${HOST}" '
  rm -rf "${HOME}/.cache/chromium" || true
  if systemctl cat renewvan-cage.service >/dev/null 2>&1; then
    sudo systemctl restart renewvan-cage.service
  else
    echo "renewvan-cage.service not installed: run bin/bootstrap-kiosk-pi.sh, or reload the browser by hand" >&2
  fi
'

echo "==> Deploy complete"
