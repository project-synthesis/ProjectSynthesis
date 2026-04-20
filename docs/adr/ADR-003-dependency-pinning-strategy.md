# ADR-003: Dependency Pinning Strategy

**Status:** Accepted
**Date:** 2026-03-25

## Context

Five Python packages used `>=` version ranges, and all frontend packages used `^` caret ranges. This allows silent version drift between environments, potentially introducing breaking changes or security vulnerabilities.

## Decision

Pin all dependencies to exact versions:
- Python: `==` pins in `requirements.txt` for all packages
- Frontend: Remove `^` from `package.json`, commit `package-lock.json`, CI uses `npm ci --frozen-lockfile`

## Update Workflow

1. `pip install --upgrade <package>` (or `npm update <package>`)
2. Run tests
3. Update pin in requirements.txt / package.json
4. Commit with message: `chore(deps): update <package> to X.Y.Z`

## Consequences

- Builds are fully reproducible across environments
- Dependency updates are explicit and reviewable
- Trade-off: manual update effort (mitigated by future Dependabot/Renovate integration)

## Implementation status

**Shipped with two documented exceptions.**

- `backend/requirements.txt` — 23 packages pinned with `==`. Two packages use `>=` by design: `packaging>=24.0` (stdlib-adjacent, ABI-stable) and `hnswlib>=0.8.0` (native-extension compatibility across numpy/scikit-learn minor versions). Both are intentional and called out in the file header comment.
- `frontend/package.json` + `frontend/package-lock.json` — zero `^`/`~` ranges; lockfile committed.
- CI uses `npm ci --frozen-lockfile` as specified.

No Dependabot/Renovate integration yet — the follow-up remains on the roadmap.
