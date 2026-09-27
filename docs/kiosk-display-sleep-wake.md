# Kiosk display sleep/wake

The kiosk touchscreen can be put to sleep (physical panel power-off) and woken
by touch — mirroring the Victron GUI-v2 behaviour on the Cerbo GX display in
the same van.

## MQTT contract

Both the command and state topics share the [`kiosk-display-power`
schema](../schema/kiosk-display-power.schema.json): a plain JSON string,
either `"on"` or `"off"`.

| Topic | Direction | Retained | Description |
|---|---|---|---|
| `renewvan/kiosk/display/power` | kiosk node → consumers | **yes** | Current display power state, published after each successful command execution. Initialised from the live `vcgencmd` reading on node startup. |
| `renewvan/kiosk/display/power/set` | dashboard → kiosk node | no | Command: `"on"` or `"off"`. The kiosk node executes the OS-level call and publishes the result to the state topic only on success. |

The `kiosk` segment is a new domain on the renewvan bus, sitting alongside
`tank`, `battery`, and `relay`. The `display/power` leaf is the only topic
defined so far; the namespace is reserved for future kiosk-specific topics.

## Kiosk node (`renewvan/node-kiosk`)

A small process running on the Pi host **outside** the Docker compose stack
(it needs direct access to `vcgencmd` and the GPU firmware mailbox, which
require privileged device passthrough inside a container). Deployed as a
systemd service.

### Responsibilities

- Subscribe to `renewvan/kiosk/display/power/set`.
- On `"off"`: run `vcgencmd display_power 0`; publish `"off"` retained on
  success.
- On `"on"`: run `vcgencmd display_power 1`; publish `"on"` retained on
  success.
- At startup: probe `vcgencmd display_power` (no-arg call reads current
  state). If unavailable, log an error and continue — the node must not crash
  the dashboard or block the broker. If available, publish the current state
  retained so the topic reflects reality after a node restart.

### Pluggable command

The OS-level command (`vcgencmd display_power`) is configurable so that the
Wayland-compositor fallback (`wlopm --off` / `wlopm --on`) requires only a
config change, not a code change.

### Systemd sequencing

The kiosk node's unit must declare `After=mosquitto.service` (or the compose
service that surfaces the broker) to avoid the same boot-order race documented
in `docs/pi-agent-sudo-setup.md` and resolved for Chromium in the
`07-kiosk-power-loss-recovery` ticket.

## Dashboard (`renewvan/dashboard`)

Two additions:

1. **Sleep button** — in a dedicated Settings panel (new surface; the sleep
   button is its only item in this version). Publishes `"off"` to the command
   topic.

2. **Sleeping overlay** — a fullscreen black layer rendered above all content
   when the state topic reads `"off"` (including on page load if the retained
   value is `"off"`). The first `pointerdown`/`touchstart` on the overlay
   publishes `"on"` to the command topic and removes the overlay; that waking
   touch is swallowed (not forwarded to the document beneath), matching
   Venus OS GUI-v2's documented behaviour.

The dashboard never calls `vcgencmd` directly; it speaks MQTT only.

## Hardware note

Before relying on touch-to-wake in production: confirm that the Official Touch
Display 2's digitizer reports events to the compositor while
`vcgencmd display_power 0` has cut the backlight. Victron's own GX displays do
(confirmed in `.scratch/renewvan-kiosk-distro/issues/08-research-venus-gui-v2-display-sleep-wake.md`),
but this specific panel needs a live check. If the digitizer goes silent with
the backlight off, touch-to-wake is not viable; a physical GPIO button or an
alternative mechanism would be needed.

## Design decisions

- **Manual button only, no idle timeout.** The dashboard is a read-only
  live-data display; a timer that hides it while the occupant is still
  consulting it would be worse than no auto-sleep at all.
- **Retained state topic, not `localStorage`.** The Chromium profile on this
  kiosk Pi has been observed to crash (see power-loss recovery ticket); a
  retained MQTT topic survives any browser restart without blinking the display
  back on.
- **Physical panel power-off, not CSS dim.** `vcgencmd display_power`
  physically cuts the panel's backlight/signal, the same mechanism Venus OS
  uses on its own screens. No parasitic draw while sleeping.
