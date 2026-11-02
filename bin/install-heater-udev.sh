#!/usr/bin/env bash
# Pi-side half of `bin/bootstrap-pi.sh --with-heater`: makes /dev/ttyAUTOTERM
# exist for driver-autoterm. bootstrap-pi.sh copies this file to the Pi and runs
# it over `ssh -t`, so `sudo` can prompt and the adapter question can be answered.
# Idempotent: a re-run with nothing to change touches nothing.
#
# What it does:
#   1. finds the FTDI USB-serial adapters (0403:6001) and their serial numbers.
#      One -> used. Several -> lists them and asks (a few tries, never guesses).
#      None -> says to plug it in. Adapters with no serial, or several sharing
#      one serial, cannot be matched by a rule: they are reported, not guessed.
#   2. writes /etc/udev/rules.d/99-renewvan-autoterm.rules, matching that serial
#      (so a second FTDI adapter never takes the heater's name), with sudo.
#   3. reloads udev and re-triggers tty devices so the symlink appears now. This
#      also happens on a re-run whose rule is current but whose symlink is missing.
#   4. adds the login user to `dialout` so that user can open the device itself
#      (e.g. `stty -F /dev/ttyAUTOTERM` when debugging). The group is not what
#      lets the driver container reach it; the container gets the device through
#      its compose `devices:` entry.
#   5. warns, instead of claiming success, if /dev/ttyAUTOTERM is still missing.
#
# Test hooks (defaults are the real Pi locations):
#   USB_SYSFS=/sys/bus/usb/devices  UDEV_RULES_DIR=/etc/udev/rules.d
#   DEV_DIR=/dev                    HEATER_USER=<login user> (default: whoami)
set -euo pipefail

USB_SYSFS="${USB_SYSFS:-/sys/bus/usb/devices}"
UDEV_RULES_DIR="${UDEV_RULES_DIR:-/etc/udev/rules.d}"
DEV_DIR="${DEV_DIR:-/dev}"
HEATER_USER="${HEATER_USER:-$(whoami)}"
RULE_FILE="${UDEV_RULES_DIR}/99-renewvan-autoterm.rules"

serials=()
noserial=0
for dev in "${USB_SYSFS}"/*; do
  [[ -r "${dev}/idVendor" && -r "${dev}/idProduct" ]] || continue
  [[ "$(<"${dev}/idVendor")" == "0403" && "$(<"${dev}/idProduct")" == "6001" ]] || continue
  serial=""
  if [[ -r "${dev}/serial" ]]; then
    serial="$(<"${dev}/serial")"
  fi
  if [[ -n "${serial}" ]]; then
    serials+=("${serial}")
  else
    noserial=$((noserial + 1))
  fi
done

if [[ ${noserial} -gt 0 ]]; then
  echo "WARNING: ${noserial} FTDI adapter(s) have no serial number and cannot be matched by a udev rule; ignoring them." >&2
fi

if [[ ${#serials[@]} -eq 0 ]]; then
  if [[ ${noserial} -gt 0 ]]; then
    echo "ERROR: the only FTDI adapter(s) found have no serial, so a rule cannot match them." >&2
  else
    echo "ERROR: no FTDI USB-serial adapter (0403:6001) found." >&2
    echo "Plug the heater's USB adapter into the Pi, then re-run this command." >&2
  fi
  exit 1
fi

dups="$(printf '%s\n' "${serials[@]}" | sort | uniq -d)"
if [[ -n "${dups}" ]]; then
  echo "ERROR: several FTDI adapters have the same serial (${dups//$'\n'/, })." >&2
  echo "A serial-matching rule would match all of them, so no rule was written." >&2
  echo "Unplug the extra adapter(s) with that serial and re-run." >&2
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
  SERIAL=""
  for _ in 1 2 3; do
    choice=""
    read -r -p "Which one is the heater? [1-${#serials[@]}] " choice || break
    if [[ "${choice}" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#serials[@]} )); then
      SERIAL="${serials[$((choice - 1))]}"
      break
    fi
    echo "Not a valid choice: '${choice}'." >&2
  done
  if [[ -z "${SERIAL}" ]]; then
    echo "ERROR: no valid choice made; not writing a rule." >&2
    echo "Unplug the other adapters or re-run and pick a number from the list." >&2
    exit 1
  fi
fi

RULE="# Heater USB-serial adapter -> /dev/ttyAUTOTERM (written by bin/bootstrap-pi.sh --with-heater)
SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"0403\", ATTRS{idProduct}==\"6001\", ATTRS{serial}==\"${SERIAL}\", SYMLINK+=\"ttyAUTOTERM\", GROUP=\"dialout\", MODE=\"0660\""

reload_udev() {
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=tty --action=add
  sudo udevadm settle || true
}

if [[ -r "${RULE_FILE}" && "$(<"${RULE_FILE}")" == "${RULE}" ]]; then
  echo "==> udev rule already up to date"
  if [[ ! -e "${DEV_DIR}/ttyAUTOTERM" ]]; then
    echo "==> ${DEV_DIR}/ttyAUTOTERM is missing; reloading udev"
    reload_udev
  fi
else
  echo "==> Writing ${RULE_FILE}"
  printf '%s\n' "${RULE}" | sudo tee "${RULE_FILE}" >/dev/null
  reload_udev
fi

if [[ " $(id -nG "${HEATER_USER}") " == *" dialout "* ]]; then
  echo "==> ${HEATER_USER} already in dialout"
else
  echo "==> Adding ${HEATER_USER} to dialout (applies at next login)"
  sudo usermod -aG dialout "${HEATER_USER}"
fi

if [[ -e "${DEV_DIR}/ttyAUTOTERM" ]]; then
  echo "==> Heater adapter ${SERIAL} -> /dev/ttyAUTOTERM"
else
  echo "WARNING: rule installed for ${SERIAL}, but ${DEV_DIR}/ttyAUTOTERM does not exist." >&2
  echo "Replug the adapter (or reboot), then re-run this command." >&2
fi
