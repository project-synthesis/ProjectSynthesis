#!/usr/bin/env bash
# PreToolUse hook — runs svelte-check before git push and gh pr create commands.
#
# Claude Code passes the full tool context on stdin as JSON:
#   { "tool_name": "Bash", "tool_input": { "command": "..." }, ... }
#
# Exit 0  → allow the tool call to proceed.
# Exit 2  → block the tool call (Claude treats this as a blocking error).
# stdout  → shown to the user.

set -euo pipefail

INPUT=$(cat)

# Extract the bash command being run.
COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    cmd = d.get('tool_input', d).get('command', '')
    print(cmd)
except Exception:
    pass
" 2>/dev/null || true)

# Gate: only act on git push or gh pr create commands.
IS_GIT_PUSH=false
IS_GH_PR=false

if printf '%s' "$COMMAND" | grep -qE 'git[[:space:]]+push'; then
  IS_GIT_PUSH=true
fi
if printf '%s' "$COMMAND" | grep -qE 'gh[[:space:]]+pr[[:space:]]+create'; then
  IS_GH_PR=true
fi

if [[ "$IS_GIT_PUSH" == false && "$IS_GH_PR" == false ]]; then
  exit 0
fi

# ── Locate frontend directory ────────────────────────────────────────────────
FRONTEND_DIR="frontend"
if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "⚠  frontend/ directory not found — skipping svelte-check."
  exit 0
fi

# ── Describe what triggered the check ────────────────────────────────────────
if [[ "$IS_GH_PR" == true ]]; then
  ACTION="PR creation"
elif [[ "$IS_GIT_PUSH" == true ]]; then
  ACTION="push"
fi

# ── Run svelte-check ─────────────────────────────────────────────────────────
echo "Running svelte-check before ${ACTION}..."
echo ""

if (cd "$FRONTEND_DIR" && npx svelte-check --tsconfig ./tsconfig.json); then
  echo ""
  echo "✓ svelte-check passed — proceeding with ${ACTION}."
  exit 0
else
  echo ""
  echo "✗ svelte-check failed. Fix the errors above before ${ACTION}."
  exit 2
fi
