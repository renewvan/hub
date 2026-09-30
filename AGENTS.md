# renewvan Hub

Open-source, vendor-agnostic campervan control hub (MQTT broker + shared device-model schema + deployment compose). See `CONTEXT.md` for domain vocabulary, `README.md` for install, `docs/architecture.md` for the layered design.

## Scope boundary

This repo hosts schema, deployment compose, docs, and `plugins/` — host-level Pi integrations that run as systemd services outside Docker (e.g. kiosk display power bridge, tailscale status). It never hosts node/logger/dashboard source: `tank`, `battery`, `logger`, `dashboard` in `docker-compose.yml` are pinned, independently-published images from their own `renewvan/*` repos, never built locally (see `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`). Don't add a Dockerfile or build step for them here; bump a service by editing its `image:` tag directly in `docker-compose.yml` instead — that's the single git-tracked source of truth, not `.env`.

## Verify

`./schema/validate-examples.sh` validates every fixture under `schema/examples/` against its entity's JSON Schema (`*.valid.json` must pass, `*.invalid.json` must fail). Run after any schema change.

## Agent skills

### Issue tracker

Local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`CONTEXT.md` + `docs/adr/` at the repo root). See `docs/agents/domain.md`.
