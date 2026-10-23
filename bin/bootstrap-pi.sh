#!/usr/bin/env bash
# One-time setup for a fresh Raspberry Pi target: installs Docker Engine +
# the Compose plugin if missing, and creates the directory layout
# bin/deploy.sh rsyncs into. Idempotent — safe to re-run.
#
# Usage: bin/bootstrap-pi.sh [--host <ssh-alias-or-user@host>]
#
# Runs the remote steps via `ssh -t` (not piped stdin) so `sudo` can prompt
# for a password interactively if the account isn't passwordless-sudo.
#
# Directory layout on the remote host:
#   /opt/renewvan/hub    - deployed app code (docker-compose.yml, docker/, config/, .env)
#   /etc/renewvan         - host-specific config (mosquitto ACLs, tank calibration)
#   /var/log/renewvan     - logs, if not relying purely on journald/`docker logs`
set -euo pipefail

HOST="renewvan"

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

REMOTE_SCRIPT="$(mktemp)"
trap 'rm -f "${REMOTE_SCRIPT}"' EXIT

cat > "${REMOTE_SCRIPT}" <<'EOF'
set -euo pipefail

# Host packages bin/deploy.sh and the plugins need, none of which Raspberry Pi
# OS Lite is guaranteed to ship:
#   python3-venv, python3-dev  the kiosk/tailscale venvs; evdev has no aarch64
#                              wheel for CPython 3.13, so pip compiles it and
#                              needs Python.h
#   build-essential            the compiler for that build
#   curl                       Docker's and Tailscale's installers
#   rsync                      deploy.sh rsyncs into /opt/renewvan
echo "==> Installing host packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  curl rsync python3-venv python3-dev build-essential i2c-tools

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + Compose plugin"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$(whoami)"
  echo "==> Docker installed. Log out/in (or start a new SSH session) for the docker group to take effect."
else
  echo "==> Docker already installed ($(docker --version))"
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin missing after install — check the Docker installer output above." >&2
  exit 1
fi

# Hardware buses the driver containers read. driver-tank and driver-tilt open
# /dev/i2c-1 through a compose `devices:` entry, so a missing device makes Docker
# refuse to create the container and aborts the whole `docker compose up -d`.
# Raspberry Pi OS Lite ships with the bus off and the i2c-dev module unloaded
# (raspi-config does both on the desktop image), so do it here, before deploy.sh.
#   - config.txt: the ARM I2C bus and the 1-wire overlay for the DS18B20 probes.
#     Both apply from the next boot.
#   - i2c-dev: the module that creates /dev/i2c-N. Load it now, and at every boot.
#   - dtparam at runtime: brings the bus up now so the stack can start without a
#     reboot. 1-wire has no such shortcut; driver-temperature runs without it (its
#     CPU sensor needs none) and finds the probes after the next boot.
echo "==> Enabling I2C and 1-wire"
BOOT_CONFIG="/boot/firmware/config.txt"
ensure_config_line() {
  local line="$1" pattern="$2"
  # Match an active (uncommented) line only: Pi OS ships "#dtparam=i2c_arm=on".
  if ! grep -Eq "^[[:space:]]*${pattern}" "${BOOT_CONFIG}"; then
    echo "${line}" | sudo tee -a "${BOOT_CONFIG}" >/dev/null
    echo "    added to config.txt: ${line}"
  fi
}
ensure_config_line "dtparam=i2c_arm=on" "dtparam=i2c_arm=on"
ensure_config_line "dtoverlay=w1-gpio,gpiopin=4" "dtoverlay=w1-gpio"

echo i2c-dev | sudo tee /etc/modules-load.d/renewvan-i2c.conf >/dev/null
sudo modprobe i2c-dev
sudo dtparam i2c_arm=on >/dev/null 2>&1 || true
# /dev/i2c-N is created asynchronously by udev after the bus registers.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [[ -e /dev/i2c-1 ]] && break
  sleep 1
done
if [[ ! -e /dev/i2c-1 ]]; then
  # Stop here rather than let `docker compose up` fail later with Docker's
  # "error gathering device information while adding custom device /dev/i2c-1".
  echo "ERROR: /dev/i2c-1 did not appear. The I2C setting is saved in" >&2
  echo "${BOOT_CONFIG}; reboot the Pi (sudo reboot), then re-run the install." >&2
  exit 1
fi

echo "==> Creating directory layout"
sudo mkdir -p /opt/renewvan/hub /etc/renewvan /var/log/renewvan
sudo chown -R "$(whoami)":"$(whoami)" /opt/renewvan /etc/renewvan /var/log/renewvan

echo "==> Bootstrap complete"
EOF

echo "==> Bootstrapping ${HOST}"
scp -q "${REMOTE_SCRIPT}" "${HOST}:/tmp/renewvan-bootstrap.sh"
ssh -t "${HOST}" "bash /tmp/renewvan-bootstrap.sh; status=\$?; rm -f /tmp/renewvan-bootstrap.sh; exit \$status"
