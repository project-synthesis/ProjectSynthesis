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
# In a git worktree, node_modules may not exist. Resolve to the main repo's
# frontend dir if the local one lacks dependencies.
FRONTEND_DIR="frontend"

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "⚠  frontend/ directory not found — skipping svelte-check."
  exit 0
fi

# Check if node_modules exist locally; if not, try the main worktree
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  MAIN_WORKTREE="$(git worktree list --porcelain 2>/dev/null | head -1 | sed 's/^worktree //')"
  if [[ -n "$MAIN_WORKTREE" && -d "$MAIN_WORKTREE/frontend/node_modules" ]]; then
    FRONTEND_DIR="$MAIN_WORKTREE/frontend"
    echo "ℹ  Using main worktree frontend at $FRONTEND_DIR"
  else
    echo "⚠  frontend/node_modules not found (worktree without deps) — skipping svelte-check."
    echo "   Run from main repo or install: cd frontend && npm install"
    exit 0
  fi
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
