#!/usr/bin/env bash
# Fails if any code literal couples `state` with `'template'` — spec §CI grep guard.
# Docs (*.md) excluded: CHANGELOG/CLAUDE.md legitimately reference the historical state.
set -euo pipefail

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
