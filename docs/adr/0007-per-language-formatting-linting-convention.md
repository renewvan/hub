---
status: accepted
---

# Per-language formatting/linting convention, enforced at commit time

Every `renewvan/*` repo is a separate codebase with its own CI and release
(ADR-0001), which means there's no single root `package.json`/`pyproject.toml`
to hang one shared lint config off. Without a documented convention, each
new repo (`renewvan/mobile`'s Expo/TS, `renewvan/node-tank`'s Python, a
future repo) would pick formatting/linting tools ad hoc, and reviewers
would see unrelated style churn mixed into every diff.

## Decision

**Pick the fastest tool per language, format+lint on every commit via a
pre-commit hook, never as a CI-only afterthought:**

- **JS/TS repos**: [Prettier](https://prettier.io) for formatting +
  [oxlint](https://oxc.rs) for linting (Rust-based, ESLint-equivalent,
  far faster) — unless the repo already has
  [Biome](https://biomejs.dev) adopted (format+lint in one Rust binary),
  in which case keep Biome rather than running two overlapping
  formatters. Don't add `tslint` — deprecated since 2019, merged into
  ESLint.
- **Python repos**: [ruff](https://docs.astral.sh/ruff/) for both
  format and lint (Rust-based, replaces black+isort+flake8 with one
  tool, same speed rationale as oxlint).
- **Repos with no application code** (config/docs only, e.g.
  `renewvan/node-relay`'s ESPHome YAML): Prettier for YAML/Markdown
  formatting only — there's no "lint" in the code-quality sense to add.
- **Enforcement**: a pre-commit hook, not just an editor setting or a CI
  check that runs minutes later. JS/TS repos use `husky` + `lint-staged`
  (runs the formatter/linter only on staged files, fast). Python and
  config-only repos use the [`pre-commit`](https://pre-commit.com)
  framework (`.pre-commit-config.yaml`) — the closest Python-ecosystem
  equivalent to `husky`+`lint-staged`, same "runs on staged files,
  blocks the commit on failure" shape. Either way: format/lint first,
  then typecheck (`tsc`/`mypy` where present), then the fast part of the
  test suite — mirroring whatever `./README.md`'s own documented verify
  command already is for that repo, not inventing a new one.
- **Shared style where the tool allows picking one**: JS/TS repos
  standardize on Prettier's `singleQuote: true, semi: false,
trailingComma: "all"` (first adopted in `renewvan/dashboard`) so
  reading code across repos doesn't context-switch on punctuation.
  Python's ruff defaults are used as-is — no repo-specific override
  without a reason.

## Consequence for a new repo

Copy the pattern from the nearest sibling of the same language
(`renewvan/dashboard` for a new JS/TS repo, `renewvan/node-tank` for a
new Python node) rather than designing formatting/linting from scratch:
its `.prettierrc`/`ruff` config in `pyproject.toml`, its pre-commit hook
setup, and its `package.json` `format`/`format:check`/`typecheck`/`test`
script names (or `pyproject.toml` equivalent) are the template.

## Rejected alternatives

- **ESLint instead of oxlint**: ESLint is the incumbent but an order of
  magnitude slower; oxlint covers the rules these repos actually use.
  Repos that already had ESLint configured with plugins oxlint doesn't
  yet cover (e.g. `renewvan/mobile`'s `eslint-config-expo`) keep ESLint
  for linting and add Prettier only for formatting, rather than forcing
  a lint-tool migration as a side effect of this convention.
- **One shared root config repo/package** consumed by every
  `renewvan/*` repo: rejected for the same reason ADR-0001 rejected git
  submodules for service code — it re-couples independent repos'
  release timing to a shared dependency for something as low-stakes as
  formatting config. A few lines of copied, repo-local config is cheaper
  than that coupling.
