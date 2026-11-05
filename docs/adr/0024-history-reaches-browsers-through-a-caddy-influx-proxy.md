---
status: accepted
---

# Dashboard history reaches InfluxDB through a same-origin Caddy proxy that holds a read-only token

The dashboard's history charts query InfluxDB from the browser (the kiosk on localhost, a phone on the LAN, a dev machine). They do it through a same-origin path, `/influx/...`, that the Caddy fronting the dashboard forwards to `influxdb:8086`. Caddy allows only `POST /api/v2/query`, strips any client `Authorization` header, caps the request body, and injects the token itself from `{env.INFLUXDB_DASHBOARD_TOKEN}`. InfluxDB and Grafana publish on `127.0.0.1` only; neither is reachable from the LAN directly. The browser never holds an Influx token.

In development the dashboard's Vite dev server proxies `/influx` to `localhost:8086` (an `ssh -L 8086:localhost:8086 renewvan` tunnel, or a local compose Influx) and attaches a read-only token from the dev `.env`, so prod and dev use identical relative URLs.

## Why

- **A token in browser JavaScript is public.** Anyone who can load the page reads it. The history charts need a client-side query path, so the token has to live on the server side of that path.
- **Caddy is already the single entry point.** Kiosk and phone both go through it, and `/mqtt` already follows the same same-origin pattern, so no CORS and no per-client configuration.
- **Least privilege per consumer.** Three tokens, all scoped to the `renewvan` bucket: the logger's is write-only, Grafana's and the dashboard's are read-only and separate, so either can be revoked on its own. The admin token is operator-only and is passed to no container.
- **Loopback publishes keep operator access.** Binding Influx and Grafana to `127.0.0.1` still allows an SSH tunnel or Tailscale for operators and for dev, without exposing either to the LAN.

## Rejected alternatives

- **Publish 8086 on the LAN and let the browser query with a read-only token.** The token ships in the bundle, and the whole Influx API (including its UI) is open to every LAN client.
- **A bespoke history API service between the dashboard and Influx.** Safest query surface, but a new service to build, pin and maintain, for a van-sized dashboard. Revisit if the proxy's query surface proves too wide.
- **Reusing Grafana's token or the admin token.** One compromise or revocation would hit unrelated consumers.

## Rules this sets

- Only `POST /api/v2/query` is forwarded; every other path and method is refused. The client's `Authorization` header is always discarded.
- The dashboard token is read-only and bucket-scoped, created by `bin/deploy.sh` alongside the logger and Grafana tokens, and kept only in `/opt/renewvan/hub/.env` (mode 600). It never appears in the repo, an image or the Caddyfile.
- Anyone on the LAN who can load the dashboard can read the whole bucket through this path, and run expensive Flux queries. This is accepted at the same trust level as the broker; whatever dashboard authentication the broker decision adopts must cover both `/mqtt` and `/influx`.
- `bin/check-exposure.sh`, run from a second host, verifies that 8086 and 3000 refuse LAN connections, that the proxy answers queries without credentials, and that a write is rejected.

## Why this is an ADR

Hard to reverse: clients and the dashboard code depend on the `/influx` path and on Influx never being LAN-published. Surprising without context: a reader will wonder why Influx has no published port and why a read token sits in the proxy rather than the app. The result of a real trade-off: direct browser access is the obvious shape and is the least work, but it puts a credential in a public bundle.

## See also

- `.scratch/security-audit/issues/03-influx-grafana-exposure-and-credentials.md`: the full decision, including secret generation and in-place rotation for deployed vans.
- `docs/adr/0023-entity-icons-are-a-shared-hub-setting.md`: another case of a static web app that cannot hold credentials or a query API of its own.
