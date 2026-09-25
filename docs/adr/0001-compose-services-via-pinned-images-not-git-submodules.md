---
status: accepted
---

# Compose services via pinned Docker images, not git submodules

`hub` deploys the backend services (`renewvan/tank`, `renewvan/battery`, `renewvan/logger`, `renewvan/dashboard`) alongside its own infra (Mosquitto, InfluxDB, Grafana), but each service lives in its own repo per the existing module boundary — `hub` hosts schema, deployment, and docs only, never driver/logger/dashboard source. We decided `hub`'s deployment compose file references each service as a pinned, independently-published Docker image (`image: <registry>/renewvan/tank:0.1.0`), never as a git submodule built from local source.

Git submodules were the obvious alternative and were rejected: a submodule pin looks like it shrinks the install command (`git clone --recurse-submodules`), but it actually leaks git-submodule mechanics into every installer and every CI run — detached-HEAD checkouts, a two-repo commit dance to bump a version, `--recurse-submodules` being easy to forget, and `hub`'s CI needing a full build matrix for languages it otherwise never touches (Python × 3, React, React Native). That re-couples the four repos' release timing to `hub`, which is exactly what the separate-repo decision was meant to avoid.

With pinned images, each `renewvan/*` repo owns its own Dockerfile, CI, and versioned release; `hub`'s interface to it is a tag string plus documented `.env` configuration. Bumping a service is a one-line tag change in `hub`'s compose file, with no cross-repo git operation. Consequence: every `renewvan/*` backend repo must publish a versioned image via its own CI — this is now part of each repo's delivery contract, not optional packaging polish.

`renewvan/mobile` (phone app) and `renewvan/relay` (ESPHome config) are explicitly out of this compose story — they're installed via their own repo-specific processes (app build/sideload; one-time flash), not `docker compose`.
