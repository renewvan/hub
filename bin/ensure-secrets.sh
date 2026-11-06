#!/usr/bin/env bash
# Idempotent "ensure secrets" step, run on the Pi by bin/deploy.sh (cwd = the hub dir, next to
# docker-compose.yml and .env). Fresh installs and upgrades share this one path.
# Decided in .scratch/security-audit/issues/03-influx-grafana-exposure-and-credentials.md.
#
#   - Generates any missing or example-valued secret into .env (mode 600): the InfluxDB admin
#     password and token, the Grafana admin password, and the three scoped InfluxDB tokens
#     (INFLUXDB_LOGGER_TOKEN write-only, INFLUXDB_GRAFANA_TOKEN and INFLUXDB_DASHBOARD_TOKEN
#     read-only, all limited to the renewvan bucket).
#   - On a van that already has data volumes, rotates in place: the DOCKER_INFLUXDB_INIT_* and
#     GF_SECURITY_ADMIN_PASSWORD variables only apply on a volume's first run, so it changes the
#     live admin token/password through the influx and grafana CLIs. It never wipes a volume.
#   - Broker accounts (docs/adr/0025): one MQTT_<CLIENT>_PASSWORD per client, the Mosquitto password
#     file and ACL in /etc/renewvan/mosquitto/, each driver's/plugin's own credential file, and the
#     Caddy login (CADDY_BASIC_AUTH_USER/HASH; its password is shown once, never stored). Clients
#     whose credential file changed are listed in <env file>.changed-clients for deploy to restart.
#     BROKER_ACL_OPEN=1 keeps the temporary `pattern readwrite renewvan/#` line and allow_anonymous true.
#   - Prints the names of the variables it changed, never their values (the one-time Caddy password
#     is the exception).
#
# Usage: bin/ensure-secrets.sh [path-to-.env]      (default ./.env)
set -euo pipefail

ENV_FILE="${1:-.env}"
INFLUX_CONTAINER="renewvan-influxdb"
GRAFANA_CONTAINER="renewvan-grafana"
INFLUX_ORG="renewvan"
INFLUX_BUCKET="renewvan"

# Placeholders and legacy defaults: never acceptable as real values.
is_example_value() {
  case "$1" in
    "" | renewvan-dev-token | renewvan-dev-only | admin | changeme | CHANGEME | pending) return 0 ;;
    *) return 1 ;;
  esac
}

[[ -f "${ENV_FILE}" ]] || { echo "ERROR: ${ENV_FILE} not found" >&2; exit 1; }
chmod 600 "${ENV_FILE}"

# shellcheck source=bin/broker-accounts.sh
source "$(dirname "${BASH_SOURCE[0]}")/broker-accounts.sh"

CHANGED=()
note() { CHANGED+=("$1"); }

# Run the influx CLI inside the container with the given token (via the environment, not argv).
influx_cli() {
  local token="$1"
  shift
  docker exec -e INFLUX_TOKEN="${token}" "${INFLUX_CONTAINER}" influx "$@"
}

json_field() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

container_exists() { docker inspect "$1" >/dev/null 2>&1; }

wait_for() { # <description> <tries> <command...>
  local what="$1" tries="$2"
  shift 2
  local i
  for ((i = 0; i < tries; i++)); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep "${ENSURE_SECRETS_POLL:-2}"
  done
  echo "ERROR: timed out waiting for ${what}" >&2
  return 1
}

influx_existed=0
container_exists "${INFLUX_CONTAINER}" && influx_existed=1
grafana_existed=0
container_exists "${GRAFANA_CONTAINER}" && grafana_existed=1

# --- 1. Admin credentials ---------------------------------------------------
old_pw="$(get INFLUXDB_ADMIN_PASSWORD)"
old_token="$(get INFLUXDB_ADMIN_TOKEN)"
new_pw="${old_pw}"
new_token="${old_token}"
if is_example_value "${old_pw}"; then new_pw="$(gen 16)"; fi
if is_example_value "${old_token}"; then new_token="$(gen 32)"; fi

# A compose command parses the whole file, and the file marks every secret as required. Until
# the scoped tokens and the Grafana password exist, hand compose a placeholder for them.
placeholder_env=()
for name in INFLUXDB_LOGGER_TOKEN INFLUXDB_GRAFANA_TOKEN INFLUXDB_DASHBOARD_TOKEN GF_SECURITY_ADMIN_PASSWORD; do
  if is_example_value "$(get "${name}")"; then placeholder_env+=("${name}=pending"); fi
done

influx_running() { docker exec "${INFLUX_CONTAINER}" influx ping >/dev/null 2>&1; }

start_influx() { # <admin password> <admin token>
  env INFLUXDB_ADMIN_PASSWORD="$1" INFLUXDB_ADMIN_TOKEN="$2" \
    "${placeholder_env[@]+"${placeholder_env[@]}"}" docker compose up -d influxdb
  wait_for "InfluxDB to answer" 30 influx_running
}

rotating=0
if [[ "${influx_existed}" == "1" ]] && { [[ "${new_token}" != "${old_token}" ]] || [[ "${new_pw}" != "${old_pw}" ]]; }; then
  rotating=1
fi

if [[ "${rotating}" == "1" ]]; then
  # The live instance still accepts the OLD admin credentials (the legacy default values,
  # or whatever .env held). Rotate through them. The volume exists, so INIT_* are ignored here.
  influx_running || start_influx "${old_pw:-x}" "${old_token:-x}"
  if [[ "${new_token}" != "${old_token}" ]]; then
    created="$(influx_cli "${old_token}" auth create --org "${INFLUX_ORG}" --all-access \
      --description "renewvan-admin" --json)"
    # InfluxDB picks the value of a token it creates; use it rather than the one we generated.
    new_token="$(printf '%s' "${created}" | json_field token)"
    old_id="$(influx_cli "${new_token}" auth list --hide-headers | awk -v t="${old_token}" '$3 == t {print $1}')"
    [[ -z "${old_id}" ]] || influx_cli "${new_token}" auth delete --id "${old_id}" >/dev/null
  fi
  if [[ "${new_pw}" != "${old_pw}" ]]; then
    influx_cli "${new_token}" user password --name admin --password "${new_pw}" >/dev/null
  fi
fi

if [[ "${new_pw}" != "${old_pw}" ]]; then set_var INFLUXDB_ADMIN_PASSWORD "${new_pw}"; note INFLUXDB_ADMIN_PASSWORD; fi
if [[ "${new_token}" != "${old_token}" ]]; then set_var INFLUXDB_ADMIN_TOKEN "${new_token}"; note INFLUXDB_ADMIN_TOKEN; fi

# --- 2. Scoped InfluxDB tokens ------------------------------------------------
need_tokens=0
for name in INFLUXDB_LOGGER_TOKEN INFLUXDB_GRAFANA_TOKEN INFLUXDB_DASHBOARD_TOKEN; do
  if is_example_value "$(get "${name}")"; then need_tokens=1; fi
done
if [[ "${need_tokens}" == "1" ]]; then
  admin_token="$(get INFLUXDB_ADMIN_TOKEN)"
  influx_running || start_influx "$(get INFLUXDB_ADMIN_PASSWORD)" "${admin_token}"
  bucket_id="$(influx_cli "${admin_token}" bucket list --name "${INFLUX_BUCKET}" --hide-headers | awk 'NR == 1 {print $1}')"
  [[ -n "${bucket_id}" ]] || { echo "ERROR: could not find the ${INFLUX_BUCKET} bucket" >&2; exit 1; }
  for spec in "INFLUXDB_LOGGER_TOKEN:write:renewvan-logger" \
    "INFLUXDB_GRAFANA_TOKEN:read:renewvan-grafana" \
    "INFLUXDB_DASHBOARD_TOKEN:read:renewvan-dashboard"; do
    IFS=: read -r name mode desc <<<"${spec}"
    if is_example_value "$(get "${name}")"; then
      created="$(influx_cli "${admin_token}" auth create --org "${INFLUX_ORG}" \
        "--${mode}-bucket" "${bucket_id}" --description "${desc}" --json)"
      set_var "${name}" "$(printf '%s' "${created}" | json_field token)"
      note "${name}"
    fi
  done
fi

# --- 3. Grafana admin password ----------------------------------------------
if is_example_value "$(get GF_SECURITY_ADMIN_PASSWORD)"; then
  gf_pw="$(gen 16)"
  set_var GF_SECURITY_ADMIN_PASSWORD "${gf_pw}"
  note GF_SECURITY_ADMIN_PASSWORD
  if [[ "${grafana_existed}" == "1" ]]; then
    # The variable only seeds a fresh grafana-data volume; this one already has a password.
    docker compose up -d grafana
    # /api/health answers only after the DB migration (11 -> 12 rewrites tables) has finished.
    wait_for "Grafana to finish starting" 60 curl -sf http://127.0.0.1:3000/api/health
    docker exec "${GRAFANA_CONTAINER}" grafana cli admin reset-admin-password "${gf_pw}" >/dev/null
  fi
fi

# --- 3b. Broker accounts (ADR 0025) -----------------------------------------------------------
new_clients=()
for key in $(client_keys); do
  var="$(client_var "${key}")"
  if is_example_value "$(get "${var}")"; then
    set_var "${var}" "$(gen 16)"
    note "${var}"
    new_clients+=("${key}")
  fi
done

caddy_password=""
if is_example_value "$(get CADDY_BASIC_AUTH_HASH)"; then
  caddy_password="$(gen 12)"
  # Single-quoted: compose would otherwise expand the $ signs of the bcrypt hash.
  set_var CADDY_BASIC_AUTH_HASH "'$(caddy_hash "${caddy_password}")'"
  set_var CADDY_BASIC_AUTH_USER "${CADDY_LOGIN_USER}"
  note CADDY_BASIC_AUTH_HASH
fi

mosq_dir="${RENEWVAN_ETC}/mosquitto"
mkdir -p "${mosq_dir}"
# A van with no ACL yet starts in the migration state (every client may still write); the cutover sets 0.
if [[ -z "$(get BROKER_ACL_OPEN)" ]]; then
  if [[ -f "${mosq_dir}/acl" ]]; then set_var BROKER_ACL_OPEN 0; else set_var BROKER_ACL_OPEN 1; fi
  note BROKER_ACL_OPEN
fi

acl_tmp="$(mktemp "${mosq_dir}/acl.XXXXXX")"
render_acl "$(get BROKER_ACL_OPEN)" >"${acl_tmp}"
if cmp -s "${acl_tmp}" "${mosq_dir}/acl"; then
  rm -f "${acl_tmp}"
else
  install_for_broker "${acl_tmp}" "${mosq_dir}/acl"
  note "mosquitto/acl"
fi

# Users in the password file must be exactly the client list; hashes are salted, so passwords are only
# rewritten when a client is new or the user set differs.
want_users="$(client_keys | sort | tr '\n' ' ')"
have_users=""
[[ -f "${mosq_dir}/passwd" ]] && have_users="$(cut -d: -f1 "${mosq_dir}/passwd" | sort | tr '\n' ' ')"
if [[ ${#new_clients[@]} -gt 0 || "${want_users}" != "${have_users}" ]]; then
  write_passwd_file
  note "mosquitto/passwd"
fi

# Entries stay until deploy has restarted the client (it clears the file), so a failed deploy loses none.
touch "${ENV_FILE}.changed-clients"
for key in $(client_keys); do
  changed="$(apply_client_credential "${key}")"
  if [[ -n "${changed}" ]] && ! grep -qx "${key}" "${ENV_FILE}.changed-clients"; then echo "${key}" >>"${ENV_FILE}.changed-clients"; fi
done

# --- 4. Refuse to continue on a placeholder or legacy default --------------------------------
broker_vars=()
for key in $(client_keys); do broker_vars+=("$(client_var "${key}")"); done
for name in INFLUXDB_ADMIN_PASSWORD INFLUXDB_ADMIN_TOKEN INFLUXDB_LOGGER_TOKEN \
  INFLUXDB_GRAFANA_TOKEN INFLUXDB_DASHBOARD_TOKEN GF_SECURITY_ADMIN_PASSWORD CADDY_BASIC_AUTH_HASH \
  "${broker_vars[@]}"; do
  if is_example_value "$(get "${name}")"; then
    echo "ERROR: ${name} in ${ENV_FILE} is empty or an example value after the secrets step" >&2
    exit 1
  fi
done

if [[ ${#CHANGED[@]} -gt 0 ]]; then
  echo "==> Generated or rotated in ${ENV_FILE} (values not shown): ${CHANGED[*]}"
else
  echo "==> Secrets already set; nothing to change"
fi

if [[ -n "${caddy_password}" ]]; then
  echo "==> Caddy login for the dashboard (shown once, only its hash is stored): user ${CADDY_LOGIN_USER} password ${caddy_password}"
fi
