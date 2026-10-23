#!/usr/bin/env bash
# One-command install of the renewvan hub on a fresh Raspberry Pi, from your
# own machine. Runs, in order:
#
#   1. preflight        ssh key login, sudo, Debian 13 / arm64, local tools
#   2. bin/bootstrap-pi.sh        Docker + /opt/renewvan layout
#   3. .env                       generated secrets, written once, never overwritten
#   4. bin/deploy.sh              compose stack, plugins, seeded node configs
#   5. bin/bootstrap-kiosk-pi.sh  cage + Chromium on the touchscreen (optional)
#
# Idempotent: safe to re-run. An existing .env (and so its InfluxDB password)
# is never touched, because InfluxDB applies its init credentials only to an
# empty data volume and a regenerated password would lock the logger out.
#
# Usage: bin/install.sh --host <ssh-alias-or-user@host> [options]
#
#   --host <h>               the Pi, as an ssh alias or user@host (required)
#   --venus-host <ip>        Venus OS device (Cerbo GX etc.); omit if the van has none
#   --venus-portal-id <id>   its portal ID; asked for if --venus-host is given without it
#   --public-host <h>        hostname/IP phones and laptops use to reach the Pi
#                            (default: the Pi's own hostname, as `hostname -I` reports)
#   --no-kiosk               skip the touchscreen session (headless hub)
#   --no-rotate              skip the 180 degree panel rotation
#   --yes                    never prompt; fail instead if something is missing
set -euo pipefail

HOST=""
VENUS_HOST=""
VENUS_PORTAL_ID=""
PUBLIC_HOST=""
KIOSK=1
ROTATE=1
ASSUME_YES=0

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:?--host needs a value}"; shift 2 ;;
    --venus-host) VENUS_HOST="${2:?--venus-host needs a value}"; shift 2 ;;
    --venus-portal-id) VENUS_PORTAL_ID="${2:?--venus-portal-id needs a value}"; shift 2 ;;
    --public-host) PUBLIC_HOST="${2:?--public-host needs a value}"; shift 2 ;;
    --no-kiosk) KIOSK=0; shift ;;
    --no-rotate) ROTATE=0; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h | --help) usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage 1 ;;
  esac
done

[[ -n "${HOST}" ]] || { echo "--host is required" >&2; usage 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Values below are substituted into .env with sed, and the portal ID / hosts end
# up in URLs and env files. Restrict them to what a hostname, IP or Venus portal
# ID can legitimately contain, instead of trying to escape sed metacharacters.
valid() { [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; }
for pair in "venus-host:${VENUS_HOST}" "venus-portal-id:${VENUS_PORTAL_ID}" "public-host:${PUBLIC_HOST}"; do
  [[ -z "${pair#*:}" ]] || valid "${pair#*:}" || { echo "--${pair%%:*} has characters outside A-Z a-z 0-9 . _ : -" >&2; exit 1; }
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Preflight: fail here, not halfway through a deploy.
# ---------------------------------------------------------------------------
step "Preflight"

for tool in ssh scp rsync; do
  command -v "${tool}" >/dev/null 2>&1 || die "'${tool}' is required on this machine"
done

# BatchMode: key login only. A password prompt here would hang a script and
# every later ssh call (deploy.sh makes many) would re-prompt.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "${HOST}" true 2>/dev/null; then
  die "cannot ssh to '${HOST}' with a key. Set it up first:
  ssh-copy-id ${HOST}
(and make sure '${HOST}' resolves: an ~/.ssh/config alias or user@host)"
fi

# One round trip for everything we need to know about the Pi.
PI_INFO="$(ssh "${HOST}" 'printf "%s|%s|%s|%s|%s\n" \
  "$(. /etc/os-release && echo "${VERSION_ID:-?}")" \
  "$(uname -m)" \
  "$(id -u)" \
  "$(sudo -n true 2>/dev/null && echo yes || echo no)" \
  "$(hostname -I 2>/dev/null | cut -d" " -f1)"')"
IFS='|' read -r PI_OS PI_ARCH PI_UID PI_SUDO PI_IP <<<"${PI_INFO}"

[[ "${PI_ARCH}" == "aarch64" ]] || die "the Pi is ${PI_ARCH}; need a 64-bit OS (aarch64)"
[[ "${PI_OS}" == "13" ]] || die "the Pi runs Debian ${PI_OS}; this install is written for Raspberry Pi OS (Debian 13, trixie)"
[[ "${PI_UID}" != "0" ]] || die "ssh as a normal user, not root: the kiosk session runs as that user"
if [[ "${PI_SUDO}" != "yes" ]]; then
  [[ "${ASSUME_YES}" == "0" ]] || die "passwordless sudo is required with --yes (see docs/pi-agent-sudo-setup.md)"
  echo "note: sudo will prompt for a password during the install"
fi
echo "ok: ${HOST} is Debian ${PI_OS} ${PI_ARCH}, ip ${PI_IP:-unknown}"

# ---------------------------------------------------------------------------
# Venus OS: the one thing that cannot be guessed. Ask once, or skip.
# ---------------------------------------------------------------------------
if [[ -n "${VENUS_HOST}" && -z "${VENUS_PORTAL_ID}" ]]; then
  [[ "${ASSUME_YES}" == "0" ]] || die "--venus-host needs --venus-portal-id with --yes"
  read -r -p "Venus OS portal ID (Cerbo GX: Settings > VRM online portal > VRM portal ID): " VENUS_PORTAL_ID
  [[ -n "${VENUS_PORTAL_ID}" ]] || die "portal ID is required with --venus-host"
  valid "${VENUS_PORTAL_ID}" || die "portal ID has characters outside A-Z a-z 0-9 . _ : -"
fi

PUBLIC_HOST="${PUBLIC_HOST:-${PI_IP}}"
[[ -n "${PUBLIC_HOST}" ]] || die "could not work out the Pi's address; pass --public-host"

# ---------------------------------------------------------------------------
# 2. Docker + directory layout
# ---------------------------------------------------------------------------
step "Bootstrapping the Pi (Docker, /opt/renewvan)"
bin/bootstrap-pi.sh --host "${HOST}"

# bootstrap-pi.sh adds the user to the `docker` group, which only applies to NEW
# login sessions. deploy.sh opens fresh ssh connections, so it normally just
# works, but a ControlMaster/ControlPersist in the user's ssh config would reuse
# the old session and fail later with a confusing "permission denied" on the
# docker socket. Check with a forced-fresh connection and say what to do.
if ! ssh -o ControlPath=none -o ControlMaster=no "${HOST}" 'docker info >/dev/null 2>&1'; then
  die "docker is installed but not usable by the ssh user yet. Close any open ssh
connection to '${HOST}' (ssh -O exit ${HOST}) and re-run bin/install.sh"
fi

# ---------------------------------------------------------------------------
# 3. .env: written once, with generated secrets.
# ---------------------------------------------------------------------------
step "Preparing .env"
if ssh "${HOST}" 'test -f /opt/renewvan/hub/.env'; then
  echo "keeping the existing /opt/renewvan/hub/.env (never overwritten)"
else
  # Generated on this machine, sent over ssh stdin: the secrets never appear in
  # a process list or shell history on the Pi.
  # 96 random bytes -> ~124 alphanumerics after base64+tr, far more than the 40
  # needed, so `head` closing the pipe never truncates it. (`tr </dev/urandom`
  # piped to `head` instead dies with SIGPIPE under `set -o pipefail`: exit 141.)
  gen_secret() { head -c 96 /dev/urandom | base64 | tr -d '\n' | tr -dc 'A-Za-z0-9' | head -c "$1"; }
  INFLUX_PASSWORD="$(gen_secret 24)"
  INFLUX_TOKEN="$(gen_secret 40)"

  ENV_TMP="$(mktemp)"
  trap 'rm -f "${ENV_TMP}"' EXIT
  sed -E \
    -e "s|^INFLUXDB_ADMIN_PASSWORD=.*|INFLUXDB_ADMIN_PASSWORD=${INFLUX_PASSWORD}|" \
    -e "s|^INFLUXDB_ADMIN_TOKEN=.*|INFLUXDB_ADMIN_TOKEN=${INFLUX_TOKEN}|" \
    -e "s|^VITE_MQTT_WS_URL=.*|VITE_MQTT_WS_URL=/mqtt|" \
    -e "s|^VENUS_MQTT_HOST=.*|VENUS_MQTT_HOST=${VENUS_HOST}|" \
    -e "s|^VENUS_PORTAL_ID=.*|VENUS_PORTAL_ID=${VENUS_PORTAL_ID}|" \
    -e "s|^TANK_CONFIG_PATH=.*|TANK_CONFIG_PATH=/etc/renewvan/tank/config.ini|" \
    -e "s|^GPS_CONFIG_PATH=.*|GPS_CONFIG_PATH=/etc/renewvan/gps/config.ini|" \
    -e "s|^TEMPERATURE_CONFIG_PATH=.*|TEMPERATURE_CONFIG_PATH=/etc/renewvan/temperature/config.ini|" \
    -e "s|^TEMPERATURE_STATE_PATH=.*|TEMPERATURE_STATE_PATH=/etc/renewvan/temperature/state|" \
    -e "s|^TILT_CONFIG_PATH=.*|TILT_CONFIG_PATH=/etc/renewvan/tilt/config.ini|" \
    -e "s|^TILT_STATE_PATH=.*|TILT_STATE_PATH=/etc/renewvan/tilt/state|" \
    -e "s|^WATTCYCLE_CONFIG_PATH=.*|WATTCYCLE_CONFIG_PATH=/etc/renewvan/wattcycle/config.ini|" \
    -e "s|^DASHBOARD_BASEMAP_PATH=.*|DASHBOARD_BASEMAP_PATH=/etc/renewvan/dashboard/basemap.pmtiles|" \
    -e "s|^MOSQUITTO_CONFIG_PATH=.*|MOSQUITTO_CONFIG_PATH=/etc/renewvan/mosquitto|" \
    .env.example >"${ENV_TMP}"

  # Create the file 0600 before any secret is written into it.
  ssh "${HOST}" 'umask 077 && cat > /opt/renewvan/hub/.env' <"${ENV_TMP}"
  echo "wrote /opt/renewvan/hub/.env with generated InfluxDB credentials"
fi

# ---------------------------------------------------------------------------
# 4. Stack, plugins, node configs
# ---------------------------------------------------------------------------
step "Deploying the stack"
bin/deploy.sh --host "${HOST}"

# ---------------------------------------------------------------------------
# 5. Touchscreen session
# ---------------------------------------------------------------------------
if [[ "${KIOSK}" == "1" ]]; then
  step "Installing the kiosk session"
  KIOSK_ARGS=(--host "${HOST}")
  [[ "${ROTATE}" == "1" ]] || KIOSK_ARGS+=(--no-rotate)
  bin/bootstrap-kiosk-pi.sh "${KIOSK_ARGS[@]}"
else
  step "Skipping the kiosk session (--no-kiosk)"
fi

step "Done"
cat <<EOF
Dashboard:   http://${PUBLIC_HOST}   (Grafana: http://${PUBLIC_HOST}:3000)
InfluxDB credentials are in /opt/renewvan/hub/.env on the Pi.

Still manual, because each needs a decision or a login only you can make:
  - Tailscale:  ssh ${HOST} sudo tailscale up
  - Tank calibration (sensor_min/max): edit /etc/renewvan/tank/config.ini, then
    docker compose restart node-tank
  - Tilt axis mapping, if the GY-521 is not mounted face-down: /etc/renewvan/tilt/config.ini
  - Battery BLE address for driver-wattcycle: [device] mac in /etc/renewvan/wattcycle/config.ini (the service stays off until it is set)
  - Broker authentication is OFF (anonymous). See .env.example and hub issue 49
    before exposing the Pi beyond your own network.
EOF
if [[ "${KIOSK}" == "1" ]]; then
  echo "  - Reboot the Pi once to start the touchscreen session:  ssh ${HOST} sudo reboot"
fi
