#!/usr/bin/env bash
# Pi-side half of `bin/bootstrap-pi.sh --with-heater`: makes /dev/ttyAUTOTERM
# exist for driver-autoterm. bootstrap-pi.sh copies this file to the Pi and runs
# it over `ssh -t`, so `sudo` can prompt and the adapter question can be answered.
# Idempotent: a re-run with nothing to change touches nothing.
#
# What it does:
#   1. finds the FTDI USB-serial adapters (0403:6001) and their serial numbers.
#      One -> used. Several -> lists them and asks. None -> says to plug it in.
#   2. writes /etc/udev/rules.d/99-renewvan-autoterm.rules, matching that serial
#      (so a second FTDI adapter never takes the heater's name), with sudo.
#   3. reloads udev and re-triggers tty devices so the symlink appears now.
#   4. adds the login user to `dialout` so the driver container can open it.
#
# Test hooks (defaults are the real Pi locations):
#   USB_SYSFS=/sys/bus/usb/devices  UDEV_RULES_DIR=/etc/udev/rules.d
#   HEATER_USER=<login user>        (default: whoami)
set -euo pipefail

USB_SYSFS="${USB_SYSFS:-/sys/bus/usb/devices}"
UDEV_RULES_DIR="${UDEV_RULES_DIR:-/etc/udev/rules.d}"
HEATER_USER="${HEATER_USER:-$(whoami)}"
RULE_FILE="${UDEV_RULES_DIR}/99-renewvan-autoterm.rules"

serials=()
for dev in "${USB_SYSFS}"/*; do
  [[ -r "${dev}/idVendor" && -r "${dev}/idProduct" && -r "${dev}/serial" ]] || continue
  [[ "$(<"${dev}/idVendor")" == "0403" && "$(<"${dev}/idProduct")" == "6001" ]] || continue
  serial="$(<"${dev}/serial")"
  [[ -n "${serial}" ]] && serials+=("${serial}")
done

if [[ ${#serials[@]} -eq 0 ]]; then
  echo "ERROR: no FTDI USB-serial adapter (0403:6001) found." >&2
  echo "Plug the heater's USB adapter into the Pi, then re-run this command." >&2
  exit 1
fi

if [[ ${#serials[@]} -eq 1 ]]; then
  SERIAL="${serials[0]}"
  echo "==> Found one FTDI adapter: ${SERIAL}"
else
  echo "Several FTDI adapters found:"
  for i in "${!serials[@]}"; do
    echo "  $((i + 1))) ${serials[$i]}"
  done
  choice=""
  read -r -p "Which one is the heater? [1-${#serials[@]}] " choice || true
  if ! [[ "${choice}" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#serials[@]} )); then
    echo "ERROR: no valid choice made; not writing a rule." >&2
    echo "Unplug the other adapters or re-run and pick a number from the list." >&2
    exit 1
  fi
  SERIAL="${serials[$((choice - 1))]}"
fi

RULE="# Heater USB-serial adapter -> /dev/ttyAUTOTERM (written by bin/bootstrap-pi.sh --with-heater)
SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"0403\", ATTRS{idProduct}==\"6001\", ATTRS{serial}==\"${SERIAL}\", SYMLINK+=\"ttyAUTOTERM\", GROUP=\"dialout\", MODE=\"0660\""

if [[ -r "${RULE_FILE}" && "$(<"${RULE_FILE}")" == "${RULE}" ]]; then
  echo "==> udev rule already up to date"
else
  echo "==> Writing ${RULE_FILE}"
  printf '%s\n' "${RULE}" | sudo tee "${RULE_FILE}" >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=tty --action=add
fi

if [[ " $(id -nG "${HEATER_USER}") " == *" dialout "* ]]; then
  echo "==> ${HEATER_USER} already in dialout"
else
  echo "==> Adding ${HEATER_USER} to dialout (applies at next login)"
  sudo usermod -aG dialout "${HEATER_USER}"
fi

echo "==> Heater adapter ${SERIAL} -> /dev/ttyAUTOTERM"
