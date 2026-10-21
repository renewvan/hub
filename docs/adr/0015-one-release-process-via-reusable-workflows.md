---
status: accepted
---

# One release process for every renewvan repo, as reusable workflows in hub

Every repo that ships something is released the same way: from the Actions tab (or `gh workflow run release.yml -f bump=minor`), one `workflow_dispatch` run computes the next version from the latest stable tag, stamps it where the repo keeps one, tags that commit, creates the GitHub release with generated notes, then builds what the repo ships. The workflow logic lives once, in this repo's `.github/workflows/`, and each repo carries a short caller that passes what differs.

| Reusable (in hub) | Does                                                                                            | Called by                                        |
| ----------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `release.yml`     | Version, tag, GitHub release; then `kind: image` (multi-arch GHCR image) or `kind: firmware`    | every repo that ships                            |
| `python-test.yml` | `pytest` on Python 3.12                                                                         | every Python repo's `test.yml`                   |
| `manifest.yml`    | Validate `manifest.json` against the hub `device-manifest` schema, plus the "no `version`" gate | every `driver-*` repo that has a `manifest.json` |

`kind` is the one real axis of difference:

- **`image`**: `driver-*`, `logger`, `dashboard`. A Device repo also sets `device-version-arg` so the tag becomes the `DEVICE_VERSION` build arg that ends up in the retained Device info; `logger`, `dashboard` and `driver-demo` publish no Device info and do not.
- **`firmware`**: `driver-esp32-relay`. It has no image. The release is a tag and a GitHub release with generated notes, and the job compiles the tagged tree as a gate. **It uploads no binaries**: WiFi, MQTT and the OTA password are compiled in from `secrets.yaml`, so a CI build carries the example placeholders, cannot join a van's network, and would publish a known OTA password if attached to a public release. The tag is the firmware's `version`, the same value `scripts/flash.sh` stamps from `git describe` when you flash from that tag.

## Why

- **Seven hand-copied release workflows had already drifted** (`driver-demo` and `logger` lacked the build arg, which is correct, but gps and tilt had their own `test.yml` and `validate-manifest.sh` variants, and three manifest gates existed). The ESP32 relay had no release at all.
- **A hand-typed tag on `release: created` is how a wrong version ships.** Computing the version from the latest tag removes it; the dashboard already did this and it is now the model everywhere.
- **The manifest gate must move with the contract it checks.** A caller pins the hub commit of the workflow and passes the same SHA as `hub-ref`, so the gate and the schema are bumped together. Four repos had a `HUB_REF` pinned to a hub commit from before the Node-to-Device rename, and their Manifest check failed on `main` after it.
- **Stable releases are cut from `main`; a pre-release can come from any branch.** A pre-release is tagged on a throwaway commit that is never pushed to a branch, and never takes `:latest`.

## Rules this sets

- A repo's own `release.yml` is a `workflow_dispatch` caller of `renewvan/hub/.github/workflows/release.yml@<sha>`. It does not reimplement versioning.
- Callers pin hub **by commit SHA**, never `@main`: a hub change must not alter every repo's release unannounced. Adopting a newer workflow is one deliberate edit per repo.
- The latest stable tag is the version source. A repo that must carry the version in a file (`dashboard`'s `package.json`) opts in with `stamp-package-json`; Python repos keep the `0.0.0` placeholder in `pyproject.toml`, which nothing reads.
- Stable releases rely on the push-to-`main` CI run for tests; only a pre-release re-runs `test-command`.
- Every caller grants `contents: write` **and** `packages: write`, including `kind: firmware` callers that publish no package. GitHub validates the entire called workflow before running it, and a caller that grants less than any called job requests fails with `startup_failure` and no jobs.
- The workflow pushes the release commit to `main` with the default token, so `main` must not be branch-protected. If protection is ever enabled, the `cut` job needs a token that may bypass it.

## Consequences

- Releasing no longer needs a hand-created GitHub release or a hand-typed tag, and the `release: created` trigger is gone.
- Hub is a runtime dependency of every repo's CI. It is public, so callers need no access setting; if it were made private, the other repos would need Actions sharing enabled.
- The reusable `release.yml` is only exercised by a real release. It is verified by releasing one repo first, not by a local linter (none is installed).

## See also

- `docs/adr/0014-node-to-device-rename.md`: the rename whose stale `HUB_REF` pins exposed the drift.
- `docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`: the pinned images these releases produce.
- `docs/adr/0007-per-language-formatting-linting-convention.md`: the hooks every commit runs.
