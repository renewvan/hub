#!/usr/bin/env bash
# One-time setup for a fresh Raspberry Pi target: installs Docker Engine +
# the Compose plugin if missing, and creates the directory layout
# bin/deploy.sh rsyncs into. Idempotent — safe to re-run.
#
# Usage: bin/bootstrap-pi.sh [--host <ssh-alias-or-user@host>] [--with-heater]
#
#   --with-heater   also install the udev rule that makes /dev/ttyAUTOTERM exist
#                   for driver-autoterm (see bin/install-heater-udev.sh). Plug the
#                   heater's USB adapter in first. Re-run it to swap the adapter.
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
WITH_HEATER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --with-heater)
      WITH_HEATER=1
      shift
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
#   curl, gnupg, ca-certificates   fetch and verify Docker's and Tailscale's apt signing keys
#   rsync                      deploy.sh rsyncs into /opt/renewvan
echo "==> Installing host packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  curl gnupg ca-certificates rsync python3-venv python3-dev build-essential i2c-tools

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + Compose plugin from Docker's signed apt repo"
  # No `curl | sh`: the vendor key lands in /etc/apt/keyrings and the source is bound to it with
  # signed-by=. Fingerprint from https://docs.docker.com/engine/install/debian/ (9DC8 5822 ...).
  DOCKER_KEY_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  actual_fpr="$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '/^fpr:/ {print $10; exit}')"
  if [[ "${actual_fpr}" != "${DOCKER_KEY_FPR}" ]]; then
    sudo rm -f /etc/apt/keyrings/docker.asc
    echo "ERROR: Docker apt key fingerprint is ${actual_fpr}, expected ${DOCKER_KEY_FPR}" >&2
    exit 1
  fi
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
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
# /opt/renewvan/plugins and /opt/renewvan/venvs hold code systemd runs, some as root: they are
# root-owned (bin/deploy.sh writes them with sudo), so only hub/ is chowned recursively here.
sudo chown "$(whoami)":"$(whoami)" /opt/renewvan
sudo chown -R "$(whoami)":"$(whoami)" /opt/renewvan/hub /etc/renewvan /var/log/renewvan
if [[ -d /opt/renewvan/plugins ]]; then sudo chown -R root:root /opt/renewvan/plugins; sudo chmod -R go-w /opt/renewvan/plugins; fi
if [[ -d /opt/renewvan/venvs ]]; then sudo chown -R root:root /opt/renewvan/venvs; sudo chmod -R go-w /opt/renewvan/venvs; fi

# driver-wattcycle runs as a fixed non-root uid and talks to BlueZ over the host's system D-Bus.
# This policy lets that uid reach only org.bluez, so a compromise of the driver cannot drive
# systemd (or anything else) on the bus. Keep the uid in step with docker-compose.yml.
echo "==> Installing the driver-wattcycle D-Bus policy"
sudo tee /etc/dbus-1/system.d/renewvan-wattcycle.conf >/dev/null <<'DBUS'
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="10001">
    <allow send_destination="org.bluez"/>
    <allow send_destination="org.freedesktop.DBus"/>
    <deny send_destination="org.freedesktop.systemd1"/>
  </policy>
</busconfig>
DBUS
sudo systemctl reload dbus 2>/dev/null || true

echo "==> Bootstrap complete"
EOF

# Copy a local script to the Pi, run it over `ssh -t` (so sudo can prompt), clean up.
# The remote path is a fresh mktemp -d, not a fixed /tmp name another local user could pre-create.
run_remote() {
  local script="$1" dir
  dir="$(ssh "${HOST}" 'mktemp -d')"
  scp -q "${script}" "${HOST}:${dir}/script.sh"
  ssh -t "${HOST}" "bash ${dir}/script.sh; status=\$?; rm -rf ${dir}; exit \$status"
}

echo "==> Bootstrapping ${HOST}"
run_remote "${REMOTE_SCRIPT}"

# The unprivileged user the kiosk browser and display bridge run as; bin/deploy.sh refuses to run
# without it. Created here so a headless hub (no kiosk session) deploys too.
run_remote "$(dirname "$0")/ensure-kiosk-user.sh"

if [[ "${WITH_HEATER}" == "1" ]]; then
  echo "==> Installing heater udev rule on ${HOST}"
  run_remote "$(dirname "$0")/install-heater-udev.sh"
fi
