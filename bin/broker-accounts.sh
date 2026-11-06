#!/usr/bin/env bash
# Sourced by bin/ensure-secrets.sh and bin/rotate-secret.sh (never run directly): the broker account
# model of docs/adr/0025-broker-has-per-client-accounts-and-anonymous-read-only.md.
#
# One Mosquitto account per client. The username is the client key; the password lives in .env as
# MQTT_<KEY>_PASSWORD. From .env this file derives everything else: the Mosquitto password file and
# ACL in ${RENEWVAN_ETC}/mosquitto/, the five drivers' config.ini [mqtt] section, and the host
# plugins' env files. Callers define ENV_FILE first.
#
# ADR 0025 sketches `pattern write renewvan/%u/#` for every driver, but a driver's entity topics are
# renewvan/<entity>/..., not renewvan/<driver>/... (wattcycle, venus and vedirect write `battery`,
# autoterm writes `heater`), and autoterm, vedirect and the hub plugin are clients too. So each
# account lists the namespaces it really writes (client_writes below).

RENEWVAN_ETC="${RENEWVAN_ETC:-/etc/renewvan}"
BROKER_GID="${BROKER_GID:-1883}" # the eclipse-mosquitto image's mosquitto group
BROKER_CONTAINER="${BROKER_CONTAINER:-renewvan-mosquitto}"
CADDY_LOGIN_USER="renewvan"

# key:kind:target[:unit]. kind = config.ini (the driver reads /etc/renewvan/<key>/config.ini, not the
# environment), compose (MQTT_USERNAME/PASSWORD from compose; target is the service), or envfile (a host
# plugin's /etc/renewvan/<target>; unit is its systemd service). `command` is the dashboard's public
# account; esp32-<name> board accounts (below) are flashed, never written from the Pi; `reader` is the read-only login for people and tools
# (MQTT Explorer, a dev dashboard) and for bin/check-broker-auth.sh: it has no process to restart.
BROKER_CLIENTS=(
  "tank:config.ini:driver-tank"
  "gps:config.ini:driver-gps"
  "temperature:config.ini:driver-temperature"
  "tilt:config.ini:driver-tilt"
  "wattcycle:config.ini:driver-wattcycle"
  "autoterm:config.ini:driver-autoterm"
  "vedirect:config.ini:driver-vedirect"
  "logger:compose:logger"
  "venus:compose:driver-venus"
  "router:compose:driver-router"
  "demo:compose:driver-demo"
  "kiosk:envfile:kiosk.env:renewvan-kiosk"
  "tailscale:envfile:tailscale.env:renewvan-tailscale"
  "hub:envfile:hub.env:renewvan-hub"
  "command:compose:dashboard"
  "reader:reader:"
)

# Board accounts (ADR 0027, hub#162): one account per ESP32 board, named by its Driver key esp32-<name>.
# The boards are the space-separated <name>s in BROKER_ESP32_BOARDS in .env (the same name chosen when the
# board is flashed); each gets MQTT_ESP32_<NAME>_PASSWORD, and the account may write only
# renewvan/esp32-<name>/# plus the relay topics listed in client_writes.
BOARD_NAME_RE='^[a-z][a-z0-9]*(-[a-z0-9]+)*$' # the Driver slug (schema/relay.schema.json definitions/id)

# The checked board list, one <name> per line. Fails closed: when any entry is bad (not a slug, repeated) it
# prints the error on stderr and NO board at all, so no consumer (ACL, password file, check, rotation) can
# act on a half-valid list. Nothing when .env is absent or the variable is empty. The value is read the way
# compose's env_file reads it (one pair of surrounding quotes removed) and split without globbing.
board_names() {
  [[ -n "${ENV_FILE:-}" && -f "${ENV_FILE}" ]] || return 0
  local raw names=() name seen=" "
  raw="$(get BROKER_ESP32_BOARDS)"
  if [[ "${raw}" =~ ^\"(.*)\"$ || "${raw}" =~ ^\'(.*)\'$ ]]; then raw="${BASH_REMATCH[1]}"; fi
  read -r -a names <<<"${raw}" # read does not glob
  for name in ${names[@]+"${names[@]}"}; do
    if [[ ! "${name}" =~ ${BOARD_NAME_RE} || "${seen}" == *" ${name} "* ]]; then
      printf "ERROR: BROKER_ESP32_BOARDS entry '%s' is not a unique lowercase slug (a-z, 0-9, single hyphens); no board accounts are created until it is fixed\n" "${name}" >&2
      return 1
    fi
    seen+="${name} "
  done
  for name in ${names[@]+"${names[@]}"}; do printf '%s\n' "${name}"; done
}

validate_boards() { board_names >/dev/null; } # for the caller that must stop on a bad list

is_board_key() { [[ "$1" == esp32-?* ]]; }

client_keys() {
  local spec name
  for spec in "${BROKER_CLIENTS[@]}"; do printf '%s\n' "${spec%%:*}"; done
  for name in $(board_names); do printf 'esp32-%s\n' "${name}"; done
  return 0
}

client_spec() { # <key> -> the full spec line
  local spec name
  if is_board_key "$1"; then
    for name in $(board_names); do
      if [[ "esp32-${name}" == "$1" ]]; then
        printf '%s:esp32:' "$1"
        return 0
      fi
    done
  fi
  for spec in "${BROKER_CLIENTS[@]}"; do
    if [[ "${spec%%:*}" == "$1" ]]; then
      printf '%s' "${spec}"
      return 0
    fi
  done
  return 1
}

# MQTT_<KEY>_PASSWORD (a hyphen in a board key becomes an underscore)
client_var() { printf 'MQTT_%s_PASSWORD' "$(printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_')"; }

# Topic filters each account may write. Everyone reads renewvan/# (global rule), so no read lines here.
client_writes() {
  case "$1" in
    tank | gps | temperature | tilt | router | logger | tailscale) echo "renewvan/$1/#" ;;
    wattcycle) printf '%s\n' "renewvan/wattcycle/#" "renewvan/battery/#" ;;
    venus | vedirect) printf '%s\n' "renewvan/$1/#" "renewvan/battery/#" "renewvan/solar/#" "renewvan/charger/#" ;;
    autoterm) printf '%s\n' "renewvan/autoterm/#" "renewvan/heater/#" ;;
    # Everything demo publishes except renewvan/demo/enabled/set, which only `command` may write.
    demo) printf '%s\n' "renewvan/demo/enabled" "renewvan/demo/health" "renewvan/demo/+/info" "renewvan/demo/+/+/+" ;;
    # State topics only (four segments); the five-segment .../set topics belong to `command`.
    kiosk) echo "renewvan/kiosk/display/+" ;;
    hub) printf '%s\n' "renewvan/hub/status" "renewvan/hub/primary-battery" "renewvan/hub/icons" ;;
    # A board: its own Driver namespace (info, health, last will) plus relay state, name and driver, never
    # relay/+/set. Relay ids are opaque (ADR 0027), so those three are not scoped to the board's own ids: see
    # "Relay ownership" in the ADR 0027 addendum.
    esp32-*) printf '%s\n' "renewvan/$1/#" "renewvan/relay/+/state" "renewvan/relay/+/name" "renewvan/relay/+/driver" ;;
    # Only the command topics, never state or health.
    command)
      printf '%s\n' "renewvan/relay/+/set" "renewvan/relay/+/name/set" "renewvan/kiosk/display/+/set" \
        "renewvan/heater/+/set" "renewvan/heater/+/+/set" "renewvan/demo/enabled/set" \
        "renewvan/hub/primary-battery/set" "renewvan/hub/icons/set"
      ;;
  esac
}

# Print the ACL. A `pattern` line applies to every client, authenticated or not; a `topic` line before the
# first `user` applies to anonymous clients only (checked on eclipse-mosquitto 2.1.2: authenticated drivers
# got nothing from a global `topic read`). Everyone reads renewvan/#, so the broker stays readable once
# anonymous clients are refused. $1 = 1 adds the temporary migration line, `topic readwrite renewvan/#`:
# a `topic` line applies to anonymous clients only, so it lets them keep writing while every account is
# already held to its own write lines (which the check script proves before the cutover).
render_acl() {
  local open="$1" key topic
  echo "# Generated by bin/ensure-secrets.sh from bin/broker-accounts.sh. Do not edit: changes are overwritten."
  echo "# ADR 0025: every account reads renewvan/#; each writes only what it owns."
  echo "pattern read renewvan/#"
  if [[ "${open}" == "1" ]]; then
    echo
    echo "# TEMPORARY (migration stages 1 to 4): set BROKER_ACL_OPEN=0 in .env once no anonymous publisher remains."
    echo "topic readwrite renewvan/#"
  fi
  for key in $(client_keys); do
    echo
    echo "user ${key}"
    while IFS= read -r topic; do
      [[ -z "${topic}" ]] || echo "topic write ${topic}"
    done < <(client_writes "${key}")
  done
}

get() { sed -n "s|^$1=||p" "${ENV_FILE}" | tr -d '\r' | tail -n1; }

# Rewrite the file through a 0600 temp file so a secret is never briefly world-readable.
set_var() {
  local tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  chmod 600 "${tmp}"
  { grep -v "^$1=" "${ENV_FILE}" || true; printf '%s=%s\n' "$1" "$2"; } >"${tmp}"
  mv "${tmp}" "${ENV_FILE}"
}

# Hex only: safe in sed, YAML, shell, ini and URLs.
gen() { openssl rand -hex "$1"; }

compose_image() { # <service> [override] -> the image: of that compose service
  if [[ -n "${2:-}" ]]; then
    printf '%s' "$2"
    return
  fi
  awk -v s="  $1:" '$0 == s {on = 1; next} on && /^  [a-z]/ {exit} on && $1 == "image:" {print $2; exit}' docker-compose.yml
}

# Password-file lines (user:$7$...) for every client, hashed inside the broker image so the hashing
# matches the broker. Passwords go in over stdin, never argv.
render_passwd() {
  local image key
  image="$(compose_image mosquitto "${MOSQUITTO_IMAGE:-}")"
  [[ -n "${image}" ]] || {
    echo "ERROR: cannot find the mosquitto image in docker-compose.yml" >&2
    return 1
  }
  for key in $(client_keys); do printf '%s %s\n' "${key}" "$(get "$(client_var "${key}")")"; done |
    docker run --rm -i --network none "${image}" sh -c '
      f=/tmp/passwd; : >"$f"
      while read -r u p; do mosquitto_passwd -b "$f" "$u" "$p" >/dev/null 2>&1 || exit 1; done
      cat "$f"'
}

# Install a file for the broker: owned by the admin user, group = the broker's gid, mode 640.
install_for_broker() { # <content file> <destination>
  local src="$1" dst="$2" owner
  mkdir -p "$(dirname "${dst}")"
  chmod 640 "${src}"
  owner="$(id -u)"
  if [[ -n "${ENSURE_SECRETS_CHOWN:-}" ]]; then
    ${ENSURE_SECRETS_CHOWN} "${owner}:${BROKER_GID}" "${src}"
  elif [[ "${owner}" == "0" ]]; then
    chown "0:${BROKER_GID}" "${src}"
  else
    sudo -n chown "${owner}:${BROKER_GID}" "${src}"
  fi
  # Keep the last file so bin/validate-broker-config.sh can roll back a bad one.
  if [[ -f "${dst}" ]]; then cp -p "${dst}" "${dst}.prev"; fi
  mv "${src}" "${dst}"
}

# Rewrite the password file from .env.
write_passwd_file() {
  local dir="${RENEWVAN_ETC}/mosquitto" tmp
  mkdir -p "${dir}"
  tmp="$(mktemp "${dir}/passwd.XXXXXX")"
  render_passwd >"${tmp}" || {
    rm -f "${tmp}"
    return 1
  }
  install_for_broker "${tmp}" "${dir}/passwd"
}

# Set the credential in an ini-style [mqtt] section or a KEY=VALUE env file, preserving everything else.
# Writes through sudo when the admin user cannot write the file (a root-owned plugin env file).
set_in_file() { # <kind: ini|env> <file> <user> <password>
  local kind="$1" file="$2" user="$3" pass="$4" tmp
  [[ -f "${file}" ]] || return 1
  tmp="$(mktemp)"
  CRED_PASS="${pass}" python3 - "${kind}" "${file}" "${user}" >"${tmp}" <<'PY'
import os, sys

kind, path, user = sys.argv[1:4]
password = os.environ["CRED_PASS"]
lines = open(path).read().splitlines()
if kind == "env":
    want = {"MQTT_USERNAME": user, "MQTT_PASSWORD": password}
    out = [ln for ln in lines if ln.split("=", 1)[0].strip() not in want]
    out += [f"{k}={v}" for k, v in want.items()]
else:
    want = {"username": user, "password": password}
    out, in_mqtt, seen, placed = [], False, set(), False

    def flush():
        for k in want:
            if k not in seen:
                out.append(f"{k} = {want[k]}")

    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_mqtt:
                flush()
            in_mqtt = s.lower() == "[mqtt]"
            placed = placed or in_mqtt
            seen = set()
            out.append(ln)
            continue
        key = None
        if "=" in s and not s.startswith(("#", ";")):
            key = s.split("=", 1)[0].strip().lower()
        if in_mqtt and key in want:
            out.append(f"{key} = {want[key]}")
            seen.add(key)
        else:
            out.append(ln)
    if in_mqtt:
        flush()
    if not placed:
        out += ["", "[mqtt]"] + [f"{k} = {v}" for k, v in want.items()]
print("\n".join(out))
PY
  if cmp -s "${tmp}" "${file}"; then
    rm -f "${tmp}"
    return 3 # already current
  fi
  if [[ -w "${file}" ]]; then cat "${tmp}" >"${file}"; else sudo -n tee "${file}" <"${tmp}" >/dev/null || { rm -f "${tmp}"; return 4; }; fi
  rm -f "${tmp}"
}

# Push one client's password to the place that client reads it, and print the key when that file changed
# (the client then needs a restart). A missing file (the profile is off, the plugin is not installed) is
# fine: .env stays the source of truth and the next run fills it in.
apply_client_credential() { # <key>
  local key="$1" spec kind target pass file ftype
  spec="$(client_spec "${key}")"
  IFS=: read -r _ kind target _ <<<"${spec}"
  pass="$(get "$(client_var "${key}")")"
  case "${kind}" in
    config.ini) file="${RENEWVAN_ETC}/${key}/config.ini"; ftype=ini ;;
    envfile) file="${RENEWVAN_ETC}/${target}"; ftype=env ;;
    *) return 0 ;;
  esac
  local rc=0
  set_in_file "${ftype}" "${file}" "${key}" "${pass}" || rc=$?
  case "${rc}" in
    0) echo "${key}" ;;
    1 | 3) ;; # file absent (profile off) or already current
    *) echo "ERROR: could not write ${key}'s credential to ${file} (rc=${rc})" >&2; return 1 ;;
  esac
  return 0
}

# Restart the process that holds a client's credential.
restart_client() { # <key>
  local key="$1" spec kind target unit
  spec="$(client_spec "${key}")"
  IFS=: read -r _ kind target unit <<<"${spec}"
  case "${kind}" in
    config.ini) docker compose restart "${target}" ;;
    compose) docker compose up -d --force-recreate --no-deps "${target}" ;; # a restart keeps the old environment
    envfile) sudo -n systemctl restart "${unit}" ;;
    esp32) echo "${key} is rotated by OTA only (skill renewvan-flash-esp32)" >&2 ;;
    reader) echo "reader has no process to restart: tell whoever uses it the new password" >&2 ;;
  esac
}

# Caddy's shared login (ticket 54): CADDY_BASIC_AUTH_USER plus a bcrypt hash; the plaintext is shown once.
caddy_hash() { # <password> -> bcrypt hash, via the Caddy image (password in the environment, not argv)
  local image
  image="$(compose_image caddy "${CADDY_IMAGE:-}")"
  CADDY_PW="$1" docker run --rm -e CADDY_PW --network none "${image}" \
    sh -c 'caddy hash-password --plaintext "$CADDY_PW"'
}
