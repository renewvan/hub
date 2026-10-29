#!/usr/bin/env bash
# One-time setup of the kiosk display session on a fresh Raspberry Pi OS *Lite*
# (Debian 13 / trixie) target: cage (Wayland kiosk compositor) running Chromium
# on tty1, with no display manager and no desktop. Run AFTER
# bin/bootstrap-pi.sh and bin/deploy.sh (which rsync plugins/kiosk/ to the Pi).
# Idempotent — safe to re-run.
#
# Usage: bin/bootstrap-kiosk-pi.sh [--host <ssh-alias-or-user@host>] [--no-rotate]
#
#   --no-rotate   no 180° panel rotation (cmdline.txt + touch udev rule); also
#                 removes both if an earlier run added them
#
# What it sets up on the Pi:
#   packages   cage, chromium, curl
#   session    /etc/systemd/system/renewvan-cage.service (tty1, logind seat)
#              default target = graphical.target; getty@tty1 yields to cage
#   hardware   /boot/firmware/config.txt: KMS driver + DSI panel overlay
#              /boot/firmware/cmdline.txt: panel_orientation=upside_down
#              udev: touch calibration matrix + bl_power write access
#   groups     kiosk user in video, input, render
#
# I2C and 1-wire are hub prerequisites (the tank, tilt and temperature nodes need
# them with or without a screen) and are set up by bin/bootstrap-pi.sh.
#
# Display rotation is done once, at the kernel/DRM layer. cage has no output
# transform, so there is no compositor rotation to double-flip it.
#
# Runs the remote steps via `ssh -t` (not piped stdin) so `sudo` can prompt for
# a password if the account isn't passwordless-sudo. Reboot afterwards.
set -euo pipefail

HOST="renewvan"
ROTATE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --no-rotate)
      ROTATE=0
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

# Unquoted heredoc delimiter would expand locally; quote it and pass the
# options in as environment instead.
cat > "${REMOTE_SCRIPT}" <<'EOF'
set -euo pipefail

KIOSK_USER="$(whoami)"
PLUGIN_DIR="/opt/renewvan/plugins/kiosk"
BOOT_DIR="/boot/firmware"

if [[ ! -f "${PLUGIN_DIR}/renewvan-cage.service" ]]; then
  echo "${PLUGIN_DIR}/renewvan-cage.service is missing — run bin/deploy.sh first" >&2
  exit 1
fi

echo "==> Installing packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  cage chromium curl

echo "==> Adding ${KIOSK_USER} to hardware groups"
for g in video input render; do
  if getent group "${g}" >/dev/null; then
    sudo usermod -aG "${g}" "${KIOSK_USER}"
  fi
done

echo "==> Boot config (${BOOT_DIR}/config.txt)"
# Append a line only if no active (uncommented) line already matches it.
ensure_config_line() {
  local line="$1" pattern="$2"
  if ! grep -Eq "^[[:space:]]*${pattern}" "${BOOT_DIR}/config.txt"; then
    echo "${line}" | sudo tee -a "${BOOT_DIR}/config.txt" >/dev/null
    echo "    added: ${line}"
  fi
}
ensure_config_line "dtoverlay=vc4-kms-v3d" "dtoverlay=vc4-kms-v3d"
ensure_config_line "dtoverlay=vc4-kms-dsi-7inch" "dtoverlay=vc4-kms-dsi-7inch"

if [[ "${ROTATE}" == "1" ]]; then
  echo "==> Panel rotation 180° (kernel DRM layer)"
  # cmdline.txt must stay ONE line; edit in place rather than appending a line.
  if ! grep -q 'panel_orientation=upside_down' "${BOOT_DIR}/cmdline.txt"; then
    sudo cp -n "${BOOT_DIR}/cmdline.txt" "${BOOT_DIR}/cmdline.txt.pre-renewvan"
    sudo sed -i '1 s|[[:space:]]*$| video=DSI-1:800x480M@60D,panel_orientation=upside_down|' \
      "${BOOT_DIR}/cmdline.txt"
    echo "    cmdline.txt updated (backup: cmdline.txt.pre-renewvan)"
  fi
  # The ft5x06 controller ignores the KMS panel_orientation, so rotate its
  # coordinates to match.
  sudo tee /etc/udev/rules.d/99-ft5x06-rotation.rules >/dev/null <<'RULE'
# Rotate touch 180° to match panel_orientation=upside_down kernel setting
SUBSYSTEM=="input", ATTRS{name}=="10-0038 generic ft5x06 (00)", \
  ENV{LIBINPUT_CALIBRATION_MATRIX}="-1 0 1 0 -1 1"
RULE
else
  # Undo an earlier rotated run: a stale touch rule would leave touch flipped
  # against an upright display, and a stale panel_orientation the reverse.
  echo "==> No panel rotation (removing any earlier rotation)"
  sudo rm -f /etc/udev/rules.d/99-ft5x06-rotation.rules
  if grep -q 'panel_orientation=upside_down' "${BOOT_DIR}/cmdline.txt"; then
    sudo cp -n "${BOOT_DIR}/cmdline.txt" "${BOOT_DIR}/cmdline.txt.pre-renewvan"
    sudo sed -i '1 s|,panel_orientation=upside_down||' "${BOOT_DIR}/cmdline.txt"
    echo "    cmdline.txt updated (backup: cmdline.txt.pre-renewvan)"
  fi
fi

# bl_power is root-writable only by default; kiosk.py runs as the kiosk user.
sudo tee /etc/udev/rules.d/99-renewvan-backlight.rules >/dev/null <<'RULE'
SUBSYSTEM=="backlight", ACTION=="add", \
  RUN+="/bin/chgrp video /sys/class/backlight/%k/bl_power", \
  RUN+="/bin/chmod g+w /sys/class/backlight/%k/bl_power"
RULE

# The Pi's HDMI ports expose CEC remote-control input (vc4-hdmi-0/-1). libinput
# classes them "keyboard pointer", so cage registers a pointer on the seat and
# draws a mouse cursor on a kiosk whose only real input is the touchscreen. cage
# hides the cursor when the seat has no pointer capability; nothing here uses CEC.
sudo tee /etc/udev/rules.d/99-renewvan-ignore-hdmi-cec.rules >/dev/null <<'RULE'
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="vc4-hdmi-*", ENV{LIBINPUT_IGNORE_DEVICE}="1"
RULE
sudo udevadm control --reload-rules
# Both rules match ACTION=="add" (backlight) or device properties (touch), which
# udev evaluates when a device appears. The panel is already registered by now,
# so without this the rules only take effect after the next reboot and the first
# "sleep display" from the dashboard fails with Permission denied on bl_power.
sudo udevadm trigger --action=add --subsystem-match=backlight
sudo udevadm trigger --action=add --subsystem-match=input
sudo udevadm settle

echo "==> Installing cage kiosk session unit"
sudo chmod +x "${PLUGIN_DIR}/renewvan-kiosk-browser.sh"
sed "s|@KIOSK_USER@|${KIOSK_USER}|" "${PLUGIN_DIR}/renewvan-cage.service" \
  | sudo tee /etc/systemd/system/renewvan-cage.service >/dev/null
sudo systemctl daemon-reload

# Replace any display manager with cage on tty1. Both are no-ops on Lite.
for dm in lightdm gdm3 sddm greetd; do
  if systemctl list-unit-files "${dm}.service" 2>/dev/null | grep -q "^${dm}"; then
    sudo systemctl disable "${dm}.service"
  fi
done
sudo systemctl set-default graphical.target
sudo systemctl enable renewvan-cage.service

echo "==> Kiosk session installed. Reboot to start it: sudo reboot"
EOF

echo "==> Bootstrapping kiosk session on ${HOST}"
scp -q "${REMOTE_SCRIPT}" "${HOST}:/tmp/renewvan-bootstrap-kiosk.sh"
ssh -t "${HOST}" "ROTATE=${ROTATE} bash /tmp/renewvan-bootstrap-kiosk.sh; status=\$?; rm -f /tmp/renewvan-bootstrap-kiosk.sh; exit \$status"
