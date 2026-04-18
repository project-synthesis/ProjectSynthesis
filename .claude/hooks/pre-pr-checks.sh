#!/usr/bin/env bash
# Unified PreToolUse hook — runs Ruff + svelte-check + template-guard before
# real `git push` or `gh pr create` invocations.
#
# Silent for all other commands, including commands that merely *mention*
# "git push" or "gh pr create" as text (e.g. grep patterns, echo args).
# Claude Code passes the full tool context on stdin as JSON:
#   { "tool_name": "Bash", "tool_input": { "command": "..." }, ... }
#
# Exit 0 → allow tool call.
# Exit 2 → block tool call (Claude treats this as a blocking error).

set -euo pipefail

INPUT="$(cat)"

# ── Fast path ────────────────────────────────────────────────────────────────
# 99% of Bash calls never mention the gate keywords at all. Skip the python
# tokenisation cost (≈30 ms) for those via a cheap substring grep.
if ! printf '%s' "$INPUT" | grep -qE 'git[[:space:]]+push|gh[[:space:]]+pr[[:space:]]+create'; then
    exit 0
fi

# ── Proper parse ─────────────────────────────────────────────────────────────
# Split on shell chain operators (&&, ||, ;, |, &) then shlex-tokenise each
# sub-command so we ONLY match real invocations — not substring hits inside
# grep patterns, echo strings, or quoted args.
GATED=$(python3 - "$INPUT" 2>/dev/null <<'PY'
import json, re, shlex, sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
cmd = ((data.get("tool_input") or data) or {}).get("command", "") or ""
parts = re.split(r"\s*(?:&&|\|\||;|\|(?!\|))\s*", cmd)
for p in parts:
    p = p.strip()
    if not p:
        continue
    try:
        tokens = shlex.split(p)
    except ValueError:
        continue
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if len(tokens) >= 2 and tokens[0] == "git" and tokens[1] == "push":
        print("yes"); break
    if len(tokens) >= 3 and tokens[0] == "gh" and tokens[1] == "pr" and tokens[2] == "create":
        print("yes"); break
PY
) || GATED=""

[[ "$GATED" == "yes" ]] || exit 0

# ── Gate matched — run all pre-PR checks ─────────────────────────────────────
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$PROJECT_ROOT"

MAIN_WORKTREE="$(git worktree list --porcelain 2>/dev/null | head -1 | sed 's/^worktree //')"
FAILED=0

resolve_ruff() {
    if [[ -x "$PROJECT_ROOT/backend/.venv/bin/ruff" ]]; then
        echo "$PROJECT_ROOT/backend/.venv/bin/ruff"; return 0
    fi
    if [[ -n "$MAIN_WORKTREE" && -x "$MAIN_WORKTREE/backend/.venv/bin/ruff" ]]; then
        echo "$MAIN_WORKTREE/backend/.venv/bin/ruff"; return 0
    fi
    command -v ruff 2>/dev/null || true
}

resolve_frontend() {
    if [[ -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
        echo "$PROJECT_ROOT/frontend"; return 0
    fi
    if [[ -n "$MAIN_WORKTREE" && -d "$MAIN_WORKTREE/frontend/node_modules" ]]; then
        echo "$MAIN_WORKTREE/frontend"; return 0
    fi
    return 1
}

echo "Running pre-PR checks..."
echo

# ── 1. Ruff ──────────────────────────────────────────────────────────────────
echo "[1/3] Ruff"
RUFF="$(resolve_ruff)"
if [[ -z "$RUFF" ]]; then
    echo "      skipped (ruff not found)"
elif "$RUFF" check "$PROJECT_ROOT/backend/app/" "$PROJECT_ROOT/backend/tests/"; then
    echo "      ✓ passed"
else
    echo "      ✗ failed"
    FAILED=1
fi
echo

# ── 2. svelte-check ──────────────────────────────────────────────────────────
echo "[2/3] svelte-check"
if FRONTEND="$(resolve_frontend)"; then
    if (cd "$FRONTEND" && npx svelte-check --tsconfig ./tsconfig.json); then
        echo "      ✓ passed"
    else
        echo "      ✗ failed"
        FAILED=1
    fi
else
    echo "      skipped (frontend/node_modules not found)"
fi
echo

# ── 3. Template guard ────────────────────────────────────────────────────────
echo "[3/3] Template guard"
if bash "$PROJECT_ROOT/.claude/hooks/pre-pr-template-guard.sh"; then
    echo "      ✓ passed"
else
    echo "      ✗ failed"
    FAILED=1
fi
echo

if [[ "$FAILED" -eq 0 ]]; then
    echo "✓ All pre-PR checks passed."
    exit 0
else
    echo "✗ Pre-PR checks failed. Fix errors above before pushing."
    exit 2
fi
