# Prompt: enable https for the kiosk dashboard on the Raspberry Pi

Paste this to an agent running on (or with SSH access to) the Raspberry Pi that hosts the hub.

---

You are setting up HTTPS for the renewvan kiosk dashboard, served from a Raspberry Pi on the LAN. Goal: an Android phone can open `https://<pi-address>`, trust a local root CA once, and install the dashboard as a PWA. There is no public domain and no internet exposure. Do not add any public DNS, Let's Encrypt, or port forwarding.

## Context you need

- The hub is a Docker Compose stack in this repo (`docker-compose.yml`, root of this repo). It runs the `dashboard` service (nginx, serves the SPA on container port 80, published to the Pi's loopback only as `127.0.0.1:80` for the local kiosk browser) and a `caddy` service that publishes host port `443` and terminates TLS.
- Caddy is configured by `../dashboard/docker/Caddyfile` (sibling repo, mounted read-only into the caddy container). It uses `tls internal`: Caddy acts as its own local CA and signs a server cert for the host in `DASHBOARD_HOST`.
- The compose `caddy` service requires `DASHBOARD_HOST` (`${DASHBOARD_HOST:?...}`). Compose fails if it is unset.
- The dashboard PWA's service worker only registers in a secure context. Plain `http://<lan-ip>` will not install. https with a trusted root is required.

## Steps

1. **Confirm prerequisites.**
   - Docker and the Compose plugin are installed: `docker --version && docker compose version`.
   - The sibling repo exists at `../dashboard/docker/Caddyfile` relative to this repo. If not, stop and report the path problem; do not create it.
   - Port 443 is free on the host: `ss -ltn | grep ':443 '` should show nothing. If something else owns it, stop and report which process.

2. **Determine the Pi's LAN address.**
   - Prefer the LAN IP: `hostname -I | awk '{print $1}'`. Use that IP, not a guess.
   - Do not use `localhost`, `127.0.0.1`, or a public IP.

3. **Set `DASHBOARD_HOST`.**
   - In this repo's `.env` (copy from `.env.example` if `.env` is missing), set `DASHBOARD_HOST=<lan-ip>`. Keep the rest of `.env` as is.
   - Do not commit `.env`. Confirm it is in `.gitignore` before finishing.

4. **Validate the compose file.**
   - Run `docker compose config --quiet` and confirm exit 0.
   - Confirm the `caddy` service publishes only `443:443`. The `dashboard` service must publish only `127.0.0.1:80:80` (loopback), never `0.0.0.0:80`.

5. **Start the stack.**
   - `docker compose up -d dashboard caddy`.
   - Check: `docker compose ps` shows both `renewvan-dashboard` and `renewvan-caddy` running.
   - Check Caddy logs: `docker compose logs caddy --tail 50`. Expect "serving initial configuration" and no errors about the Caddyfile or the `DASHBOARD_HOST` site address.

6. **Verify https locally on the Pi.**
   - Fetch the root CA from the container:
     `docker cp renewvan-caddy:/data/caddy/pki/authorities/local/root.crt ./renewvan-root.crt`
   - Confirm the server cert is signed by it and serves the dashboard:
     `curl -sS --cacert ./renewvan-root.crt https://<lan-ip>/ | head -c 300`
     Expect the dashboard's HTML (`<div id="root">`). If you get a TLS error, the SAN/IP in the cert does not match `DASHBOARD_HOST`; check the caddy logs and the `DASHBOARD_HOST` value.
   - Confirm the manifest and service worker are served:
     `curl -sS --cacert ./renewvan-root.crt -o /dev/null -w '%{http_code} %{content_type}\n' https://<lan-ip>/manifest.webmanifest` should be `200` with a JSON manifest content type.
     `curl -sS --cacert ./renewvan-root.crt -o /dev/null -w '%{http_code}\n' https://<lan-ip>/sw.js` should be `200`.

7. **Hand off the root CA.**
   - Leave `renewvan-root.crt` in a known location and print its path and SHA-256 fingerprint: `openssl x509 -in ./renewvan-root.crt -noout -fingerprint -sha256`.
   - Tell the operator to copy it to the Android phone and install it (Settings → Security → Encryption & credentials → Install a certificate → CA certificate). Then open `https://<lan-ip>` in Chrome and confirm the install prompt appears.
   - Do not install the root on any device yourself.

## Constraints

- Do not change `dashboard/docker/Caddyfile`, `nginx.conf`, or the dashboard source. If the Caddy or nginx config needs a change, stop and report it.
- Do not publish port 80 on any interface other than loopback, and do not publish any other port from this stack.
- Do not delete the `caddy-data` volume. It holds the root CA. Deleting it forces every device to re-trust a new root.
- Do not run `docker compose down -v`.
- Do not expose MQTT, InfluxDB, or Grafana ports to the internet.

## Report back

- The `DASHBOARD_HOST` value used.
- Output of `docker compose ps` and the three `curl` checks (status and content type).
- The root CA SHA-256 fingerprint and its path.
- Anything that failed, with the exact error and the command that produced it.

Do not claim the phone install works; that step is performed by the operator on the device.
