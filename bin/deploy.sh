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

# The kiosk browser runs as the unprivileged renewvan-kiosk user (created by bin/bootstrap-kiosk-pi.sh).
# deploy.sh only deploys: it never creates or migrates that user, it refuses to continue without it.
# .scratch/security-audit/issues/04-kiosk-privilege-split.md
echo "==> Checking the kiosk user on ${HOST}"
if ! ssh "${HOST}" "id renewvan-kiosk >/dev/null 2>&1"; then
  echo "ERROR: user renewvan-kiosk does not exist on ${HOST}: run bin/bootstrap-kiosk-pi.sh first." >&2
  exit 1
fi

echo "==> Syncing compose artifacts to ${HOST}:${REMOTE_DIR}"
ssh "${HOST}" "mkdir -p ${REMOTE_DIR} && sudo mkdir -p /opt/renewvan/plugins/kiosk /opt/renewvan/plugins/tailscale /opt/renewvan/plugins/hub /opt/renewvan/venvs"
rsync -az docker-compose.yml docker-compose.lan.yml "${HOST}:${REMOTE_DIR}/"
ssh "${HOST}" "mkdir -p ${REMOTE_DIR}/bin"
rsync -az bin/vedirect-gate.sh bin/ensure-secrets.sh bin/broker-accounts.sh bin/rotate-secret.sh bin/validate-broker-config.sh bin/check-broker-auth.sh bin/cutover-broker-acl.sh "${HOST}:${REMOTE_DIR}/bin/"
rsync -az --delete docker/ "${HOST}:${REMOTE_DIR}/docker/"
rsync -az .env.example "${HOST}:${REMOTE_DIR}/.env.example"
rsync -az bin/heater-hint.sh "${HOST}:${REMOTE_DIR}/heater-hint.sh"
# caddy (docker-compose.yml) bind-mounts ../dashboard/docker/Caddyfile, which resolves to
# /opt/renewvan/dashboard/docker/Caddyfile on the Pi. Ship it from the sibling dashboard checkout:
# if the source is missing on the Pi, Docker creates a root-owned directory at that path instead.
CADDYFILE="${REPO_ROOT}/../dashboard/docker/Caddyfile"
if [[ ! -f "${CADDYFILE}" ]]; then
  echo "ERROR: ${CADDYFILE} not found: clone renewvan/dashboard next to this repo." >&2
  exit 1
fi
ssh "${HOST}" "mkdir -p /opt/renewvan/dashboard/docker"
rsync -az "${CADDYFILE}" "${HOST}:/opt/renewvan/dashboard/docker/Caddyfile"
# Plugin code is run by systemd (some of it as root), so the trees are root-owned and not writable by the
# admin login or the kiosk user: rsync runs under sudo and chowns what it writes.
PLUGIN_RSYNC=(rsync -az --delete --rsync-path="sudo rsync")
"${PLUGIN_RSYNC[@]}" --exclude='.venv' plugins/kiosk/ "${HOST}:/opt/renewvan/plugins/kiosk/"
"${PLUGIN_RSYNC[@]}" plugins/tailscale/ "${HOST}:/opt/renewvan/plugins/tailscale/"
"${PLUGIN_RSYNC[@]}" plugins/hub/ "${HOST}:/opt/renewvan/plugins/hub/"
# (--chown is not portable to macOS rsync, so ownership is fixed on the Pi.) The plugin and venv roots
# themselves are re-owned too: a Pi that predates the kiosk split still has them admin-owned.
ssh "${HOST}" "sudo chown -R root:root /opt/renewvan/plugins /opt/renewvan/venvs && sudo chmod -R go-w /opt/renewvan/plugins /opt/renewvan/venvs"
# The hub's version is a property of this checkout, not of the files rsynced: the Pi has no .git.
# Stamp it after the rsync above (which would otherwise --delete it) so renewvan-hub publishes it
# on renewvan/hub/status and Settings > General > Firmware can show it. Same string the ESP32
# flash script derives; an untagged tree gives a commit hash, never a refusal.
HUB_VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo dev)"
echo "==> Stamping hub version ${HUB_VERSION}"
ssh "${HOST}" "printf '%s\n' '${HUB_VERSION}' | sudo tee /opt/renewvan/plugins/hub/VERSION >/dev/null"

echo "==> Ensuring remote config"
ssh "${HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${REMOTE_DIR}"

mkdir -p /etc/renewvan/mosquitto /etc/renewvan/tank /etc/renewvan/gps /etc/renewvan/temperature/state /etc/renewvan/tilt/state /etc/renewvan/dashboard /etc/renewvan/wattcycle /etc/renewvan/autoterm /etc/renewvan/vedirect

# plugin/kiosk: create venv (Bookworm externally-managed Python) and install deps
# (root-owned, like the plugin code that imports it)
sudo python3 -m venv /opt/renewvan/venvs/kiosk
sudo /opt/renewvan/venvs/kiosk/bin/pip install --quiet -r /opt/renewvan/plugins/kiosk/requirements.txt
sed "s|@SERVICE_USER@|renewvan-kiosk|" /opt/renewvan/plugins/kiosk/renewvan-kiosk.service | sudo tee /etc/systemd/system/renewvan-kiosk.service >/dev/null
sudo systemctl daemon-reload
# restart (not just enable --now): an upgraded unit changes user, which a running service would not pick up
sudo systemctl enable renewvan-kiosk.service
sudo systemctl restart renewvan-kiosk.service
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
  sed -i 's|^WATTCYCLE_CONFIG_PATH=.*|WATTCYCLE_CONFIG_PATH=/etc/renewvan/wattcycle/config.ini|' .env
  sed -i 's|^AUTOTERM_CONFIG_PATH=.*|AUTOTERM_CONFIG_PATH=/etc/renewvan/autoterm/config.ini|' .env
  sed -i 's|^VEDIRECT_CONFIG_PATH=.*|VEDIRECT_CONFIG_PATH=/etc/renewvan/vedirect/config.ini|' .env
  sed -i 's|^DASHBOARD_BASEMAP_PATH=.*|DASHBOARD_BASEMAP_PATH=/etc/renewvan/dashboard/basemap.pmtiles|' .env
  sed -i 's|^MOSQUITTO_CONFIG_PATH=.*|MOSQUITTO_CONFIG_PATH=/etc/renewvan/mosquitto|' .env
  # Secrets are generated by bin/ensure-secrets.sh below; the rest has working defaults.
  echo "==> Seeded ${REMOTE_DIR}/.env. Set DASHBOARD_HOST (the Pi's LAN IP) and, with Venus OS,"
  echo "    VENUS_MQTT_HOST/VENUS_PORTAL_ID there, then re-run bin/deploy.sh."
fi
chmod 600 .env

# A full-URL VITE_MQTT_WS_URL is opened by the *browser* (kiosk, phone), so it must
# name an address this Pi actually has right now. Moving the Pi to another router
# changes its DHCP IP and leaves the old one here: the dashboard then shows "Hub
# connection lost" with no data while every container is healthy. Fail loudly.
# The path form (/mqtt) names no host at all -- the dashboard resolves it against
# the page's own origin and its nginx proxies it to the broker -- so it cannot go
# stale and is not checked.
# Strip the quotes and trailing CR compose also tolerates, so a hand-edited value is judged on its content.
ws_url="\$(sed -n 's|^VITE_MQTT_WS_URL=||p' .env | tr -d '\\r' | sed -e 's/^"\\(.*\\)"\$/\\1/' -e "s/^'\\(.*\\)'\\\$/\\1/")"
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
  # Collect the Pi's names/addresses first, tolerating a failing command (hostname -f exits
  # non-zero when the hostname does not resolve): under pipefail that would otherwise fail
  # the whole match even though grep found the host.
  pi_names="\$({ hostname -I; hostname; hostname -f; echo localhost; } 2>/dev/null | tr ' ' '\\n' || true)"
  if ! printf '%s\\n' "\${pi_names}" | grep -qxF "\${ws_host}"; then
    echo "ERROR: VITE_MQTT_WS_URL points at \${ws_host}, which is not an address of this Pi (\$(hostname -I 2>/dev/null | xargs || true))." >&2
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
# Same for driver-wattcycle's key.
grep -q '^WATTCYCLE_CONFIG_PATH=' .env || echo 'WATTCYCLE_CONFIG_PATH=/etc/renewvan/wattcycle/config.ini' >> .env
# Same for driver-autoterm's key.
grep -q '^AUTOTERM_CONFIG_PATH=' .env || echo 'AUTOTERM_CONFIG_PATH=/etc/renewvan/autoterm/config.ini' >> .env
# Same for driver-vedirect's key.
grep -q '^VEDIRECT_CONFIG_PATH=' .env || echo 'VEDIRECT_CONFIG_PATH=/etc/renewvan/vedirect/config.ini' >> .env
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

if [[ ! -f /etc/renewvan/autoterm/config.ini ]]; then
  echo "Seeding /etc/renewvan/autoterm/config.ini from the repo default (sets [mqtt] host = mosquitto; commands_enabled stays false until you set it)."
  cp docker/autoterm/config.ini.default /etc/renewvan/autoterm/config.ini
fi

if [[ ! -f /etc/renewvan/vedirect/config.ini ]]; then
  echo "Seeding /etc/renewvan/vedirect/config.ini from the repo default (sets [mqtt] host = mosquitto; set [serial] port_globs to narrow which USB adapters it reads)."
  cp docker/vedirect/config.ini.default /etc/renewvan/vedirect/config.ini
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

if [[ ! -f /etc/renewvan/wattcycle/config.ini ]]; then
  echo "Seeding /etc/renewvan/wattcycle/config.ini from the repo default (sets [mqtt] host = mosquitto; set [driver] mac to the battery's BLE address, or driver-wattcycle stays off)."
  cp docker/wattcycle/config.ini.default /etc/renewvan/wattcycle/config.ini
fi

# driver-gps is behind the \`gps\` compose profile because its \`devices:\` entry
# makes Docker refuse to create the container (and compose abort the whole
# \`up -d\`) when /dev/ttyACM0 does not exist. Enable the profile only when the
# module is plugged in; re-running deploy after plugging it in starts the driver.
# Without the module, any previously created gps container is stopped so it
# doesn't crash-loop on the missing device.
if [[ -e /dev/ttyACM0 ]]; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}gps"
  echo "==> GPS device /dev/ttyACM0 present: enabling the gps profile"
else
  echo "==> No /dev/ttyACM0: skipping driver-gps (plug in the GPS module and re-run deploy to enable it)"
  docker compose rm -sf driver-gps >/dev/null 2>&1 || true
fi

# driver-autoterm is behind the \`autoterm\` compose profile for the same reason as gps: its
# \`devices:\` entry aborts the whole \`up -d\` when /dev/ttyAUTOTERM (the udev symlink from the
# driver's README) does not exist. Enable it only when the adapter is set up.
if [[ -e /dev/ttyAUTOTERM ]]; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}autoterm"
  echo "==> Heater device /dev/ttyAUTOTERM present: enabling the autoterm profile"
else
  # Skipping is fine (many vans have no heater): explain it, install no rules, keep going.
  bash ${REMOTE_DIR}/heater-hint.sh "${HOST}" || true
  docker compose rm -sf driver-autoterm >/dev/null 2>&1 || true
fi

# driver-vedirect is behind the \`vedirect\` compose profile so a van without a Victron USB cable
# runs no useless container. bin/vedirect-gate.sh checks /dev/serial/by-id against the driver's
# port_globs (its defaults, or the override in /etc/renewvan/vedirect/config.ini). Any previously
# created container is stopped otherwise.
if bash bin/vedirect-gate.sh /dev/serial/by-id /etc/renewvan/vedirect/config.ini; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}vedirect"
  echo "==> VE.Direct USB adapter present: enabling the vedirect profile"
else
  echo "==> No matching /dev/serial/by-id adapter: skipping driver-vedirect (plug in the VE.Direct cable and re-run deploy to enable it)"
  docker compose rm -sf driver-vedirect >/dev/null 2>&1 || true
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

# driver-wattcycle is behind the \`wattcycle\` compose profile: without a \`mac\` in its config.ini
# the driver exits at startup and crash-loops. Enable it only once a MAC is set; any previously
# created container is stopped otherwise.
if grep -Eq '^mac[[:space:]]*=[[:space:]]*[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}' /etc/renewvan/wattcycle/config.ini; then
  export COMPOSE_PROFILES="\${COMPOSE_PROFILES:+\${COMPOSE_PROFILES},}wattcycle"
  echo "==> Battery MAC set: enabling the wattcycle profile"
else
  echo "==> No battery MAC in /etc/renewvan/wattcycle/config.ini: skipping driver-wattcycle"
  docker compose rm -sf driver-wattcycle >/dev/null 2>&1 || true
fi

# caddy's compose entry requires DASHBOARD_HOST (\${DASHBOARD_HOST:?}). Unset, \`up -d\` aborts for
# every service, not just caddy, so refuse before pulling rather than half-deploy.
if ! grep -Eq '^DASHBOARD_HOST=.+' .env; then
  echo "ERROR: DASHBOARD_HOST in ${REMOTE_DIR}/.env is empty: set it to the Pi's LAN IP (hostname -I) and re-run." >&2
  exit 1
fi

# The Pi publishes the broker on its van-LAN address too, through docker-compose.lan.yml. Selecting
# it in .env (COMPOSE_FILE) makes every plain \`docker compose\` run on the Pi use it, not just this script.
if ! grep -q '^COMPOSE_FILE=' .env; then
  echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.lan.yml' >> .env
elif ! grep '^COMPOSE_FILE=' .env | grep -q 'docker-compose.lan.yml'; then
  echo "ERROR: COMPOSE_FILE in ${REMOTE_DIR}/.env does not include docker-compose.lan.yml; add it (docker-compose.yml:docker-compose.lan.yml) and re-run." >&2
  exit 1
fi

# The broker is published on loopback and on MQTT_BIND_ADDR only (docker-compose.lan.yml), so it is
# unreachable from an uplink interface. Seed it from DASHBOARD_HOST when that is an IPv4 address
# (an existing .env predates the key), then require it to be an address this Pi has right now:
# a stale DHCP address would otherwise make mosquitto fail to start.
if ! grep -Eq '^MQTT_BIND_ADDR=.+' .env; then
  dash_host="\$(sed -n 's|^DASHBOARD_HOST=||p' .env | tr -d '\\r' | head -n1)"
  if printf '%s' "\${dash_host}" | grep -Eq '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+\$'; then
    sed -i '/^MQTT_BIND_ADDR=/d' .env
    echo "MQTT_BIND_ADDR=\${dash_host}" >> .env
    echo "==> MQTT_BIND_ADDR not set: using DASHBOARD_HOST (\${dash_host})"
  else
    echo "ERROR: MQTT_BIND_ADDR in ${REMOTE_DIR}/.env is empty: set it to the Pi's van-LAN IPv4 address (hostname -I) and re-run." >&2
    exit 1
  fi
fi
bind_addr="\$(sed -n 's|^MQTT_BIND_ADDR=||p' .env | tr -d '\\r' | head -n1)"
if ! { hostname -I 2>/dev/null | tr ' ' '\\n'; } | grep -qxF "\${bind_addr}"; then
  echo "ERROR: MQTT_BIND_ADDR=\${bind_addr} is not an address of this Pi (\$(hostname -I 2>/dev/null | xargs || true))." >&2
  echo "       The Pi's IP probably changed (new router). Set MQTT_BIND_ADDR in ${REMOTE_DIR}/.env to its current van-LAN address and re-run." >&2
  exit 1
fi

# Caddy's 443 is published on MQTT_BIND_ADDR and the Pi's Tailscale address only (docker-compose.lan.yml),
# never every interface: /mqtt behind it can switch relays. Seed the tailnet address from tailscale, then
# require it to be an address this Pi has right now (tailscale must be logged in: sudo tailscale up).
if ! grep -Eq '^TAILSCALE_BIND_ADDR=.+' .env; then
  ts_ip="\$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  if printf '%s' "\${ts_ip}" | grep -Eq '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+\$'; then
    sed -i '/^TAILSCALE_BIND_ADDR=/d' .env
    echo "TAILSCALE_BIND_ADDR=\${ts_ip}" >> .env
    echo "==> TAILSCALE_BIND_ADDR not set: using tailscale ip (\${ts_ip})"
  else
    echo "ERROR: TAILSCALE_BIND_ADDR in ${REMOTE_DIR}/.env is empty and tailscale has no IPv4 address: run sudo tailscale up, or set it, and re-run." >&2
    exit 1
  fi
fi
ts_addr="\$(sed -n 's|^TAILSCALE_BIND_ADDR=||p' .env | tr -d '\\r' | head -n1)"
if ! { hostname -I 2>/dev/null | tr ' ' '\\n'; } | grep -qxF "\${ts_addr}"; then
  echo "ERROR: TAILSCALE_BIND_ADDR=\${ts_addr} is not an address of this Pi (\$(hostname -I 2>/dev/null | xargs || true))." >&2
  echo "       Set it to the output of 'tailscale ip -4' in ${REMOTE_DIR}/.env and re-run." >&2
  exit 1
fi

# Caddy serves its site under the Pi's MagicDNS name too (the Caddyfile lists it), and its certificate names
# it. Seed it from tailscale; leave it empty when MagicDNS is off (the Caddyfile then falls back to localhost).
if ! grep -Eq '^TAILSCALE_DNS_NAME=.+' .env; then
  ts_dns="\$(tailscale status --json 2>/dev/null | python3 -c 'import sys, json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  if printf '%s' "\${ts_dns}" | grep -Eq '^[A-Za-z0-9.-]+\$'; then
    sed -i '/^TAILSCALE_DNS_NAME=/d' .env
    echo "TAILSCALE_DNS_NAME=\${ts_dns}" >> .env
    echo "==> TAILSCALE_DNS_NAME not set: using the MagicDNS name (\${ts_dns})"
  else
    echo "==> No MagicDNS name found: Caddy will not serve a Tailscale name (set TAILSCALE_DNS_NAME to add one)"
  fi
fi

# Generate any missing or example-valued secret, create the scoped InfluxDB tokens, and rotate a van
# that still runs on legacy default values. Idempotent; never wipes a volume. Values are never printed.
echo "==> Ensuring secrets"
bash bin/ensure-secrets.sh .env

# The broker's password file and ACL (ADR 0025) are checked in a throwaway container before the running
# broker is touched; a bad ACL line would make it exit on reload. Stops the deploy on failure.
echo "==> Validating the broker config and ACL"
bash bin/validate-broker-config.sh

echo "==> docker compose pull && up -d"
docker compose pull
# --remove-orphans: containers have fixed container_name values, so a renamed service key leaves
# the old container holding the name and a plain up -d aborts half-way (ADR 0014's rename did).
docker compose up -d --remove-orphans
# New password file / ACL: a changed mosquitto.conf needs a restart (listeners and auth settings), the files
# alone a SIGHUP. Neither runs when the validation above failed (the deploy stopped there).
if [[ -f /etc/renewvan/mosquitto/mosquitto.conf.changed ]]; then
  rm -f /etc/renewvan/mosquitto/mosquitto.conf.changed
  docker compose restart mosquitto
else
  docker kill -s HUP renewvan-mosquitto >/dev/null
fi
# A node refused with rc=5 does not retry, and a driver reads its config.ini only at start: restart every
# client whose credential file ensure-secrets just changed (compose clients are recreated by up -d above,
# their environment changed). The list is rewritten on each ensure-secrets run.
if [[ -s .env.changed-clients ]]; then
  : > .env.changed-clients.failed
  while read -r client; do
    echo "==> Restarting \${client} (new broker credential)"
    ( ENV_FILE=.env; source bin/broker-accounts.sh; restart_client "\${client}" ) || { echo "WARN: could not restart \${client}; kept for the next deploy" >&2; echo "\${client}" >> .env.changed-clients.failed; }
  done < .env.changed-clients
  mv .env.changed-clients.failed .env.changed-clients
fi
# caddy bind-mounts the Caddyfile as a single file: each deploy replaces it with a new inode, which the
# running container keeps reading until it restarts. Restart it here (brief TLS blip; the root CA lives in
# the caddy-data volume, so devices keep trusting it).
docker compose restart caddy
# grafana reads its datasource provisioning only at startup, and a changed bind-mounted file does not
# make \`up -d\` recreate it. Restart so an edited datasource loads (dashboard JSON reloads on its own).
docker compose restart grafana
REMOTE

echo "==> Reloading kiosk browser"
# Fresh cache: chromium heuristic-caches index.html, which would keep serving
# the previous dashboard build after an image bump. The cache lives in the
# renewvan-kiosk user's home (/var/lib/renewvan-kiosk).
# Restarting renewvan-cage relaunches cage and therefore Chromium; if the
# unit isn't installed yet (Pi not bootstrapped with bin/bootstrap-kiosk-pi.sh)
# say so instead of silently leaving the old dashboard on screen.
ssh "${HOST}" '
  sudo rm -rf /var/lib/renewvan-kiosk/.cache/chromium || true
  if systemctl cat renewvan-cage.service >/dev/null 2>&1; then
    sudo systemctl restart renewvan-cage.service
  else
    echo "renewvan-cage.service not installed: run bin/bootstrap-kiosk-pi.sh, or reload the browser by hand" >&2
  fi
'

echo "==> Deploy complete"
