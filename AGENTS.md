# renewvan Hub

Open-source, vendor-agnostic campervan control hub (MQTT broker + shared device-model schema + deployment compose). See `CONTEXT.md` for domain vocabulary, `README.md` for install, `docs/architecture.md` for the layered design.

## Scope Boundary &amp; Multi-Repo Coordination

This repo hosts schema, deployment compose, docs, and `plugins/` — host-level Pi integrations that run as systemd services outside Docker (e.g., kiosk display power bridge, tailscale status).

While this repository itself does not host the source code for microservices (tank, battery, logger, and dashboard in `docker-compose.yml` use pinned, independently-published images from their respective `renewvan/*` repos per `docs/adr/[0001-compose-services-via-pinned-images-not-git-submodules.md](http://0001-compose-services-via-pinned-images-not-git-submodules.md)`), **agents are not restricted from working across repositories if the context or task involves them.** If a bugfix or feature spans frontend dashboard behavior, node logic, or backend services, you may navigate to and work within the relevant `renewvan/*` source repository when available in the workspace, rather than treating this host repository boundary as a blanket refusal.

For deployment changes specific to this host repo, bump a service by editing its `image:` tag directly in `docker-compose.yml` — that is the single git-tracked source of truth, not `.env`. Don't add a Dockerfile or local build step for them here.

### Code formatting/linting (new repo? read this first)

Every `renewvan/*` repo formats/lints on every commit via a pre-commit hook — never CI-only. Per `docs/adr/0007-per-language-formatting-linting-convention.md`: Prettier (or Biome, where already adopted) + oxlint for JS/TS, ruff for Python, enforced via `husky`+`lint-staged` (JS/TS) or the `pre-commit` framework (Python/config-only repos). Starting a new repo: copy the setup from the nearest sibling of the same language (`renewvan/dashboard` for JS/TS, `renewvan/node-tank` for Python) rather than designing it from scratch.

## Verify

`./schema/validate-examples.sh` validates every fixture under `schema/examples/` against its entity's JSON Schema (`*.valid.json` must pass, `*.invalid.json` must fail). Run after any schema change.

## Agent skills

### Issue tracker

Local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`CONTEXT.md` + `docs/adr/` at the repo root). See `docs/agents/domain.md`.
