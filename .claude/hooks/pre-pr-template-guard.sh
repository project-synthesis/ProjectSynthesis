#!/usr/bin/env bash
# Fails if any code literal couples `state` with `'template'` — spec §CI grep guard.
# Docs (*.md) excluded: CHANGELOG/CLAUDE.md legitimately reference the historical state.
# CWD-safe: resolves the repo root so it works when called from any directory.
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

MATCHES=$(rg --pcre2 -n \
  --type-add 'code:*.{py,ts,svelte,js,tsx,jsx,mjs,cjs,mts,cts}' \
  --type code \
  '\bstate\b[^\n]{0,32}[\x27"]template[\x27"]' \
  backend/ frontend/ 2>/dev/null || true)

if [[ -n "$MATCHES" ]]; then
  echo "BLOCKED: residual state='template' literals:"
  echo "$MATCHES"
  exit 2
fi
exit 0
