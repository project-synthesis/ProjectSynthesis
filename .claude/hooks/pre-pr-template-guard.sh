#!/usr/bin/env bash
# Fails if NEW write-side code introduces state='template' literals.
#
# Scope mirrors backend/tests/test_template_state_sweep.py (the project's
# source-of-truth ward for Task 25). Only narrow write-side constructs trip
# this guard:
#   1. state.in_([...'template'...])           — write-side filter list
#   2. _candidate_states = [...'template'...]  — write-side state enum list
#   3. state_counts.get('template'...)         — counter against retired state
#   4. {'template': template}                  — dict entry for retired state
#   5. Literal[...'template'...]               — Pydantic schema for retired state
#
# Equality checks (`body.state == "template"` rejection logic, `node.state ===
# 'template'` legacy event rendering) and read-side defensive sweeps are
# tolerated — they are intentional, load-bearing code documented in CLAUDE.md.
#
# Excludes:
#   - backend/alembic/**           — migration history (immutable)
#   - backend/tests/**             — negative tests assert rejection of this state
#   - frontend/**/*.test.ts        — Task 26 legacy-event compat tests
#   - frontend/**/*.spec.ts        — same
#   - warm_phases.py               — documented defensive sweep (CLAUDE.md)
#   - *.md                         — CHANGELOG/CLAUDE.md reference historical state
#
# CWD-safe: resolves the repo root so it works when called from any directory.
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

PATTERNS=(
  'state[^\n]{0,16}\.in_\(\s*\[[^\]]*[\x27"]template[\x27"][^\]]*\]'
  '_?candidate_states\s*=\s*\[[^\]]*[\x27"]template[\x27"][^\]]*\]'
  'state_counts\.get\(\s*[\x27"]template[\x27"]'
  '[\x27"]template[\x27"]\s*:\s*template\b'
  'Literal\[[^\]]*[\x27"]template[\x27"][^\]]*\]'
)

EXCLUDES=(
  -g '!backend/alembic/**'
  -g '!backend/tests/**'
  -g '!frontend/**/*.test.ts'
  -g '!frontend/**/*.spec.ts'
  -g '!backend/app/services/taxonomy/warm_phases.py'
)

ALL_MATCHES=""
for pat in "${PATTERNS[@]}"; do
  m=$(rg --pcre2 -n \
    --type-add 'code:*.{py,ts,svelte,js,tsx,jsx,mjs,cjs,mts,cts}' \
    --type code \
    "${EXCLUDES[@]}" \
    "$pat" \
    backend/ frontend/ 2>/dev/null || true)
  if [[ -n "$m" ]]; then
    ALL_MATCHES+="$m"$'\n'
  fi
done

if [[ -n "${ALL_MATCHES//[[:space:]]/}" ]]; then
  echo "BLOCKED: write-side state='template' literals (mirrors Task 25 ward):"
  echo "$ALL_MATCHES"
  exit 2
fi
exit 0
