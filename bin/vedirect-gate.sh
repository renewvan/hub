#!/usr/bin/env bash
# Exit 0 when a /dev/serial/by-id entry matches the VE.Direct driver's port globs, else 1.
# Gates the `vedirect` compose profile in bin/deploy.sh so a van without a Victron USB cable
# runs no useless container.
#
# Usage: vedirect-gate.sh [by-id-dir] [config.ini]
#
# The globs are driver-vedirect's default `[serial] port_globs` (keep in step with its
# config.default.ini); an uncommented `port_globs =` in config.ini replaces them.
set -euo pipefail

BY_ID="${1:-/dev/serial/by-id}"
CONFIG="${2:-}"
GLOBS="usb-Duppa_srl_*, usb-VictronEnergy_*, usb-FTDI_*"

if [[ -n "${CONFIG}" && -r "${CONFIG}" ]]; then
  override="$(sed -n 's/^[[:space:]]*port_globs[[:space:]]*=[[:space:]]*//p' "${CONFIG}" | tr -d '\r' | head -n 1)"
  if [[ -n "${override}" ]]; then GLOBS="${override}"; fi
fi

[[ -d "${BY_ID}" ]] || exit 1
IFS=',' read -r -a patterns <<<"${GLOBS}"
for pattern in "${patterns[@]}"; do
  pattern="${pattern#"${pattern%%[![:space:]]*}"}"
  pattern="${pattern%"${pattern##*[![:space:]]}"}"
  [[ -n "${pattern}" ]] || continue
  for entry in "${BY_ID}"/${pattern}; do
    [[ -e "${entry}" || -L "${entry}" ]] && exit 0
  done
done
exit 1
