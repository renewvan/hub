#!/usr/bin/env bash
# Kiosk touch input fix: stock Raspberry Pi OS labwc config breaks
# touch-drag scrolling in the kiosk Chromium in two stacked ways.
#
# Defect 1 — mouse emulation. /etc/xdg/labwc/rc.xml as shipped by Pi OS
# pins every known Pi touchscreen (the Official Touch Display's ft5x06
# controller family, plus the Touch Display 2's Goodix/ili controllers)
# to mouseEmulation="yes". labwc-config(5): with mouse emulation "all
# touch up/down/motion events are translated to mouse button and motion
# events" — the compositor then delivers wl_pointer, never wl_touch.
# Kiosk Chromium (native Wayland) sees a mouse: taps click and
# pointer-drag widgets (sliders) work, but native touch panning never
# engages, so the dashboard's scrollable areas (e.g. the Alerts tab's
# ScrollArea) cannot be scrolled by finger. Mouse wheel / trackpad from
# a remote browser is unaffected, which is why this hides from desktop
# testing.
#
# Defect 2 — phantom output mapping. The stock rules come in DSI-1 and
# DSI-2 variants (single- vs dual-panel Touch Display 2 setups). On a
# single-panel host the DSI-2 rules reference an output that does not
# exist, and native wl_touch is dropped outright when its mapped output
# is missing (observed on labwc 0.20.1: with emulation off but a bogus
# mapToOutput, no touch reaches any client — not even as a tap).
#
# Fix: flip every <touch> rule to mouseEmulation="no" (real wl_touch)
# and, when exactly one display output is connected, remap every rule's
# mapToOutput to it. Idempotent — safe to re-run on every deploy;
# re-runs are what keep the fix in place after a Pi OS update rewrites
# rc.xml.
#
# Run on the Pi host as root. deploy.sh invokes this after syncing
# plugins/kiosk/. labwc applies <touch> rules when attaching input
# devices at startup — if the script changed anything, reboot (or
# restart the labwc session) once for it to take effect.
set -euo pipefail

# labwc reads rc.xml from XDG_CONFIG_HOME/labwc FIRST and, by default,
# uses ONLY that file — a user-level rc.xml fully shadows the system one
# (this exact trap kept a system-level fix from taking effect once). So
# every rc.xml in the search path gets the same normalization.
USER_HOME="$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)"
RC_FILES=(
  "/etc/xdg/labwc/rc.xml"
  "${USER_HOME}/.config/labwc/rc.xml"
)

for RC in "${RC_FILES[@]}"; do
  [[ -e "${RC}" ]] && found=1
done
if [[ -z "${found:-}" ]]; then
  echo "fix-labwc-touch-scroll: no labwc rc.xml found — nothing to do"
  exit 0
fi
changed=0

# Connected DRM connectors → Wayland output names (card1-DSI-1 → DSI-1).
outputs="$(find /sys/class/drm -maxdepth 1 -name 'card*-*' -exec sh -c 'grep -qx connected "$1/status" 2>/dev/null && basename "$1" | sed "s/^card[0-9]*-//"' _ {} \;)"
out_count="$(echo "${outputs}" | grep -c . || true)"

for RC in "${RC_FILES[@]}"; do
  [[ -f "${RC}" ]] || continue

  if grep -q '<touch [^>]*mouseEmulation="yes"' "${RC}"; then
    cp -n "${RC}" "${RC}.pre-touch-scroll-fix" || true
    sed -i 's#\(<touch [^>]*mouseEmulation=\)"yes"#\1"no"#' "${RC}"
    echo "fix-labwc-touch-scroll: ${RC}: touch rules flipped to mouseEmulation=\"no\""
    changed=1
  fi

  if [[ "${out_count}" -eq 1 ]]; then
    only="$(echo "${outputs}" | head -1)"
    bad="$(grep -E "<touch [^>]*mapToOutput=" "${RC}" | grep -v "mapToOutput=\"${only}\"" || true)"
    if [[ -n "${bad}" ]]; then
      cp -n "${RC}" "${RC}.pre-touch-scroll-fix" || true
      sed -i "s#\(<touch [^>]*mapToOutput=\)\"[^\"]*\"#\1\"${only}\"#g" "${RC}"
      echo "fix-labwc-touch-scroll: ${RC}: touch rules remapped to sole output ${only}"
      changed=1
    fi
  elif [[ "${out_count}" -gt 1 ]]; then
    echo "fix-labwc-touch-scroll: multiple outputs connected (${outputs//${IFS:0:1}/, }) —" \
         "left mapToOutput alone; check touchscreen-to-output mapping manually" >&2
  fi
done

# Reload config so a live session picks the rules up where possible.
LABWC_PID="$(pgrep -x labwc | head -1 || true)"
if [[ -n "${LABWC_PID}" ]]; then
  kill -HUP "${LABWC_PID}"
  echo "fix-labwc-touch-scroll: labwc reloaded (pid ${LABWC_PID})"
fi

if [[ ${changed} -eq 1 ]]; then
  echo "fix-labwc-touch-scroll: changed rules — reboot once to re-attach inputs"
fi
