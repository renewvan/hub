---
status: accepted
---

# The dashboard and the mobile app live in one `apps` monorepo with a shared `@renewvan/core`

The dashboard (`renewvan/dashboard`) and the mobile app (`renewvan/mobile`) are both TypeScript clients of the same Renewvan bus, and nearly every feature lands in both. Mobile was kept in step by copying the dashboard's `src/lib`, `src/types.ts`, bus parsing and command code by hand ("change it in the dashboard first, then copy"). Nothing checked the copies, and they drifted both ways. Mobile's bus parser dropped the Driver info `ip` that the dashboard kept, so a Settings row never showed. Mobile also gained starter-battery types the dashboard lacked, and the solar algorithm differs between the two.

So the two apps move into one repo, and the code they share becomes one package instead of copies:

- **`renewvan/dashboard` is renamed `renewvan/apps`**, and keeps its stars, issues, release tags and image. The dashboard moves to `dashboard/`. Mobile moves in, with its history, to `mobile/`. Both sit at the repo root, beside `packages/`. Its open issues are transferred, and `renewvan/mobile` is archived with a pointer.
- **`packages/core`** (`@renewvan/core`) holds what both apps share, starting with the bus core: every topic, the parsers and pure reducer for retained state, the real/Demo split, the Command builders and the Demo-mode Command gate. The shared domain lib follows it there. The apps depend on it through the workspace (`workspace:*`), so it has no releases of its own.
- **Tooling** is one pnpm workspace with vite-plus (Vitest, Oxfmt, Oxlint) everywhere. Mobile alone keeps Jest (`jest-expo`) for its React Native component tests.
- **Only the TypeScript apps** go in. The hub, the Drivers, the logger and the website stay in their own repos. A Driver contributor still gets a small, focused repo (`driver-template`).
- **Artifact names do not change.** The dashboard image stays `ghcr.io/renewvan/dashboard` with `v*` tags, pinned by its `image:` tag in `docker-compose.yml` ([ADR 0001](0001-compose-services-via-pinned-images-not-git-submodules.md)). The mobile app is still built and updated through EAS, with no git tags.

## Considered options

- **Keep copying, plus a drift check** (a script in mobile that diffs the copies against a pinned dashboard commit). Rejected: it detects drift after the fact, and every shared change is still two PRs and a manual sync.
- **A separate `@renewvan/core` package repo**, released and pinned by both apps. Rejected: it removes drift but puts a release and two bump PRs between every core change and the apps. That is not worth it for a project where the same person changes the core and both apps together. Reconsider it if mobile gets its own maintainers.
- **One monorepo for everything** (hub, Drivers, apps). Rejected: the Python Drivers and the hub share no code with the apps, and Driver repos are deliberately small.

## Consequences

- The hub's reusable release workflow ([ADR 0015](0015-one-release-process-via-reusable-workflows.md)) assumes the app sits at the repo root. It needs inputs for the Dockerfile path (`dashboard/Dockerfile`, with the workspace root as build context so `packages/core` is included) and for the `package.json` to stamp (`dashboard/package.json`).
- CI runs per app with path filters, and a change to `packages/core` runs both apps' checks. Mobile's OTA workflow runs from `mobile/`, uploads without the dashboard (`.easignore`), and must keep the same fingerprint for dashboard-only dependency changes.
- More people can reach the mobile release path, so `mobile/**`, `packages/core/**` and `.github/**` are code-owned, and production OTA promotion runs in a protected GitHub Environment that holds `EXPO_TOKEN`.
- Shared dependency versions (TypeScript, mqtt, Vitest) come from the pnpm catalog. React and React Native are never put in the catalog: each app pins its own.
- The Caddyfile moved from the dashboard to the hub (`docker/caddy/Caddyfile`): it describes hub routes (login, `/mqtt`, `/influx`, `/maps`), so a hub deploy no longer needs an app checkout.
- The repo table in `home/AGENTS.md` and the repo-naming lines in `CONTEXT.md` change when the rename happens.
