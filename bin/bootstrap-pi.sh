#!/usr/bin/env bash
# One-time setup for a fresh Raspberry Pi target: installs Docker Engine +
# the Compose plugin if missing, and creates the directory layout
# bin/deploy.sh rsyncs into. Idempotent — safe to re-run.
#
# Usage: bin/bootstrap-pi.sh [--host <ssh-alias-or-user@host>]
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
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

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + Compose plugin"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$(whoami)"
  echo "==> Docker installed. Log out/in (or start a new SSH session) for the docker group to take effect."
else
  echo "==> Docker already installed ($(docker --version))"
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin missing after install — check the Docker installer output above." >&2
  exit 1
fi

echo "==> Creating directory layout"
sudo mkdir -p /opt/renewvan/hub /etc/renewvan /var/log/renewvan
sudo chown -R "$(whoami)":"$(whoami)" /opt/renewvan /etc/renewvan /var/log/renewvan

echo "==> Bootstrap complete"
EOF

echo "==> Bootstrapping ${HOST}"
scp -q "${REMOTE_SCRIPT}" "${HOST}:/tmp/renewvan-bootstrap.sh"
ssh -t "${HOST}" "bash /tmp/renewvan-bootstrap.sh; status=\$?; rm -f /tmp/renewvan-bootstrap.sh; exit \$status"
