#!/usr/bin/env bash
# Pi-side helper for bin/deploy.sh, run when /dev/ttyAUTOTERM is missing. Prints why the
# autoterm profile is skipped, the exact bootstrap command, and -- when exactly one FTDI
# adapter (0403:6001) with a serial is not yet named by the heater udev rule -- its serial.
# Installs nothing and always exits 0: many vans have no heater.
#
# Usage: heater-hint.sh [<ssh-host>]   (the host only shapes the printed command)
# Test hooks: USB_SYSFS=/sys/bus/usb/devices  UDEV_RULES_DIR=/etc/udev/rules.d
set -euo pipefail

HOST="${1:-renewvan}"
USB_SYSFS="${USB_SYSFS:-/sys/bus/usb/devices}"
RULE_FILE="${UDEV_RULES_DIR:-/etc/udev/rules.d}/99-renewvan-autoterm.rules"

cmd="bin/bootstrap-pi.sh --with-heater"
[[ "${HOST}" == "renewvan" ]] || cmd="bin/bootstrap-pi.sh --host ${HOST} --with-heater"

unmapped=()
for dev in "${USB_SYSFS}"/*; do
  [[ -r "${dev}/idVendor" && -r "${dev}/idProduct" && -r "${dev}/serial" ]] || continue
  [[ "$(<"${dev}/idVendor")" == "0403" && "$(<"${dev}/idProduct")" == "6001" ]] || continue
  serial="$(<"${dev}/serial")"
  [[ -n "${serial}" ]] || continue
  if [[ -r "${RULE_FILE}" ]] && grep -qF "ATTRS{serial}==\"${serial}\"" "${RULE_FILE}"; then
    continue
  fi
  unmapped+=("${serial}")
done

echo "==> No /dev/ttyAUTOTERM: skipping driver-autoterm (no heater set up; fine if this van has none)"
echo "    To enable it, plug the heater's USB adapter in and run from your machine:"
echo "      ${cmd}"
if [[ ${#unmapped[@]} -eq 1 ]]; then
  echo "    Found one unmapped FTDI adapter, serial ${unmapped[0]}; that command will use it."
fi
