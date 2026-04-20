#!/usr/bin/env bash
# Full release workflow: version sync → changelog migration → commit → tag → GitHub Release → dev bump.
#
# Usage:
#   ./scripts/release.sh              # release whatever version is in version.json (strip -dev)
#   ./scripts/release.sh 0.4.0        # release a specific version
#   ./scripts/release.sh --dry-run    # show what would happen without making changes
#
# Prerequisites: gh CLI authenticated, clean working tree, on main branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
cd "$ROOT"

DRY_RUN=false
VERSION_ARG=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *) VERSION_ARG="$arg" ;;
    esac
done

# ---------------------------------------------------------------------------
# Failure handling: tell the user exactly where we failed so they can clean up.
# ---------------------------------------------------------------------------
CURRENT_STEP="preflight"
on_error() {
    local exit_code=$?
    echo "" >&2
    echo "[release] ✗ FAILED at step: $CURRENT_STEP (exit $exit_code)" >&2
    echo "" >&2
    echo "Inspect the current state and clean up as needed:" >&2
    echo "  git status                    # uncommitted files" >&2
    echo "  git log --oneline -5          # recent commits" >&2
    echo "  git tag -l '${TAG:-v*}'       # local tags" >&2
    echo "  git ls-remote --tags origin   # remote tags" >&2
    if [[ -n "${TAG:-}" ]]; then
        echo "" >&2
        echo "Common recovery commands:" >&2
        echo "  git tag -d $TAG                       # delete local tag" >&2
        echo "  git push --delete origin $TAG         # delete remote tag (if pushed)" >&2
        echo "  gh release delete $TAG --yes          # delete GitHub Release (if created)" >&2
        echo "  git reset --hard HEAD~1               # undo release commit (LOCAL ONLY — verify first)" >&2
    fi
}
trap on_error ERR

# ---------------------------------------------------------------------------
# 1. Determine release version
# ---------------------------------------------------------------------------
CURRENT=$(python3 -c "import json; print(json.load(open('version.json'))['version'])")

if [[ -n "$VERSION_ARG" ]]; then
    RELEASE_VERSION="$VERSION_ARG"
else
    # Strip -dev/-rc suffix from current version
    RELEASE_VERSION=$(echo "$CURRENT" | sed 's/-dev$//' | sed 's/-rc\.[0-9]*$//')
fi

# Validate semver MAJOR.MINOR.PATCH (with optional -prerelease we don't handle here)
if ! [[ "$RELEASE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "  ✗ Invalid release version: '$RELEASE_VERSION' (expected MAJOR.MINOR.PATCH)"
    exit 1
fi

TAG="v$RELEASE_VERSION"

echo "[release] Version: $RELEASE_VERSION (tag: $TAG)"
echo "          Current: $CURRENT"

# ---------------------------------------------------------------------------
# 2. Preflight — checks that apply to dry-run too (branch, tooling, tag collision)
# ---------------------------------------------------------------------------
CURRENT_STEP="preflight (branch check)"
if [[ "$(git branch --show-current)" != "main" ]]; then
    echo "  ✗ Must be on main branch (currently on $(git branch --show-current))"
    exit 1
fi

CURRENT_STEP="preflight (gh CLI)"
if ! command -v gh &>/dev/null; then
    echo "  ✗ gh CLI not found. Install: https://cli.github.com/"
    exit 1
fi

CURRENT_STEP="preflight (gh auth)"
if ! gh auth status &>/dev/null; then
    echo "  ✗ gh CLI not authenticated. Run: gh auth login"
    exit 1
fi

CURRENT_STEP="preflight (tag collision)"
# Use exact-match check. `git tag -l PATTERN` returns only that tag when PATTERN has no globs.
if [[ -n "$(git tag -l "$TAG")" ]]; then
    echo "  ✗ Tag $TAG already exists. Delete it first or choose a different version."
    exit 1
fi

echo "  ✓ Preflight passed"

# ---------------------------------------------------------------------------
# 2b. Dry-run exits here — below this point we mutate files/commits/remote.
#     Dirty-tree check lives below so dry-run can preview with in-progress work.
# ---------------------------------------------------------------------------
if $DRY_RUN; then
    echo ""
    echo "[dry-run] Would:"
    echo "  1. Migrate docs/CHANGELOG.md: move ## Unreleased items to ## $TAG — $(date +%F)"
    echo "  2. Set version.json to $RELEASE_VERSION"
    echo "  3. Sync to backend/_version.py + frontend/package.json"
    echo "  4. Commit: release: $TAG"
    echo "  5. Tag: $TAG"
    echo "  6. Push main + tag"
    echo "  7. Create GitHub Release with changelog body"
    echo "  8. Bump to next dev version"
    if [[ -n "$(git status --porcelain)" ]]; then
        echo ""
        echo "  ⚠ Working tree is dirty — the real run will refuse until you commit/stash."
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# 2c. Mutating preflight — dirty tree + up-to-date with remote
# ---------------------------------------------------------------------------
CURRENT_STEP="preflight (dirty tree)"
if [[ -n "$(git status --porcelain)" ]]; then
    echo "  ✗ Working tree is dirty. Commit or stash changes first."
    exit 1
fi

CURRENT_STEP="preflight (remote sync)"
git fetch origin main --quiet
LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse origin/main)
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
    BEHIND=$(git rev-list --count HEAD..origin/main)
    if [[ "$BEHIND" -gt 0 ]]; then
        echo "  ✗ Local main is $BEHIND commit(s) behind origin/main. Pull first: git pull --ff-only"
        exit 1
    fi
    # Ahead is fine — the release commit will push those too.
fi

# ---------------------------------------------------------------------------
# 3. Migrate CHANGELOG: move ## Unreleased items to ## vX.Y.Z — YYYY-MM-DD
# ---------------------------------------------------------------------------
CURRENT_STEP="changelog migration"
TODAY=$(date +%F)

CHANGELOG_MIGRATED=$(RELEASE_VERSION="$RELEASE_VERSION" TODAY="$TODAY" python3 <<'PY'
import os, re, sys

version = os.environ["RELEASE_VERSION"]
today = os.environ["TODAY"]
path = "docs/CHANGELOG.md"

with open(path) as f:
    content = f.read()

# Idempotency: if the dated section already exists, do nothing.
if re.search(rf"^## v{re.escape(version)} — ", content, re.MULTILINE):
    print("already-migrated")
    sys.exit(0)

# Match the Unreleased block: header + body up to next `## ` or EOF.
m = re.search(r"^## Unreleased\s*\n(.*?)(?=^## |\Z)", content, re.DOTALL | re.MULTILINE)
if not m:
    print("no-unreleased-section", file=sys.stderr)
    sys.exit(2)

body = m.group(1).strip()
if not body:
    # Empty Unreleased — skip migration, release body will fall through to fallback.
    print("empty-unreleased")
    sys.exit(0)

new_section = f"## v{version} — {today}\n\n{body}\n\n"
new_block = f"## Unreleased\n\n{new_section}"
new_content = content[: m.start()] + new_block + content[m.end():]

with open(path, "w") as f:
    f.write(new_content)

print("migrated")
PY
)

case "$CHANGELOG_MIGRATED" in
    migrated)         echo "  ✓ CHANGELOG migrated: Unreleased → $TAG ($TODAY)" ;;
    already-migrated) echo "  ✓ CHANGELOG: $TAG section already present (idempotent)" ;;
    empty-unreleased) echo "  ⚠ CHANGELOG: ## Unreleased is empty; release body will use fallback text" ;;
    *)
        echo "  ✗ CHANGELOG migration failed: $CHANGELOG_MIGRATED"
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# 4. Update version.json to release version (strip -dev)
# ---------------------------------------------------------------------------
CURRENT_STEP="version.json write"
echo "{\"version\": \"$RELEASE_VERSION\"}" > version.json
echo "  ✓ version.json → $RELEASE_VERSION"

# ---------------------------------------------------------------------------
# 5. Sync to backend + frontend
# ---------------------------------------------------------------------------
CURRENT_STEP="sync-version.sh"
"$SCRIPT_DIR/sync-version.sh"

# ---------------------------------------------------------------------------
# 6. Extract changelog section for the release body (post-migration)
# ---------------------------------------------------------------------------
CURRENT_STEP="changelog extraction"
RELEASE_BODY=$(RELEASE_VERSION="$RELEASE_VERSION" python3 <<'PY'
import os, re
version = os.environ["RELEASE_VERSION"]
with open("docs/CHANGELOG.md") as f:
    content = f.read()

# Prefer the dated section (created by step 3).
m = re.search(rf"^## v{re.escape(version)} — .*?\n(.*?)(?=^## |\Z)", content, re.DOTALL | re.MULTILINE)
if m and m.group(1).strip():
    print(m.group(1).strip())
else:
    # Fallback: Unreleased block (e.g., when migration was skipped).
    m = re.search(r"^## Unreleased\s*\n(.*?)(?=^## |\Z)", content, re.DOTALL | re.MULTILINE)
    if m and m.group(1).strip():
        print(m.group(1).strip())
    else:
        print(f"Release {version}")
PY
)

echo "  ✓ Changelog extracted ($(echo "$RELEASE_BODY" | wc -l) lines)"

# ---------------------------------------------------------------------------
# 7. Commit + tag
#    Idempotent for the PR-merge-then-release flow: if version.json and the
#    changelog already reflect the release (landed via a merged PR), tag
#    whatever HEAD is — post-review fixes on HEAD are part of this release.
# ---------------------------------------------------------------------------
CURRENT_STEP="git commit (release)"
if [[ -z "$(git status --porcelain)" ]]; then
    # Confirm the release commit exists somewhere in the branch so we're not
    # tagging a branch that just happens to have version.json at $RELEASE_VERSION
    # by coincidence. `git log --grep -F` avoids pipe-to-grep SIGPIPE under
    # `set -e pipefail`.
    RELEASE_SHA=$(git log -F --grep="release: $TAG" --pretty=%H -1)
    if [[ -n "$RELEASE_SHA" ]]; then
        echo "  ✓ Release commit already on branch ($RELEASE_SHA) — tagging HEAD (idempotent)"
    else
        echo "  ✗ Nothing to commit and no 'release: $TAG' commit on branch"
        exit 1
    fi
else
    git add -A
    git commit -m "release: $TAG

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
fi

CURRENT_STEP="git tag"
git tag "$TAG"
echo "  ✓ Committed and tagged $TAG"

# ---------------------------------------------------------------------------
# 8. Push
# ---------------------------------------------------------------------------
CURRENT_STEP="git push (main + tags)"
git push origin main --tags
echo "  ✓ Pushed to origin"

# ---------------------------------------------------------------------------
# 9. Create GitHub Release — mark as latest only if this tag sorts highest.
# ---------------------------------------------------------------------------
CURRENT_STEP="gh release create"
HIGHEST_TAG=$(git tag -l 'v*' | sort -V | tail -1 || true)
if [[ -z "$HIGHEST_TAG" || "$HIGHEST_TAG" == "$TAG" ]]; then
    LATEST_FLAG="--latest"
else
    LATEST_FLAG="--latest=false"
fi

echo "$RELEASE_BODY" | gh release create "$TAG" \
    --title "$TAG" \
    --notes-file - \
    $LATEST_FLAG
echo "  ✓ GitHub Release created: $TAG ($LATEST_FLAG)"

# ---------------------------------------------------------------------------
# 10. Bump to next dev version (patch+1)
# ---------------------------------------------------------------------------
CURRENT_STEP="dev bump"
IFS='.' read -r MAJOR MINOR PATCH <<< "$RELEASE_VERSION"
NEXT_DEV="$MAJOR.$MINOR.$((PATCH + 1))-dev"
echo "{\"version\": \"$NEXT_DEV\"}" > version.json
"$SCRIPT_DIR/sync-version.sh"

# Seed an empty Unreleased section if it was consumed by migration.
# (Migration keeps the header; this is a no-op safeguard.)
python3 <<'PY'
import re
path = "docs/CHANGELOG.md"
with open(path) as f:
    content = f.read()
if not re.search(r"^## Unreleased\s*$", content, re.MULTILINE):
    # Insert after the top-level header block.
    content = re.sub(r"(^# Changelog.*?\n\n)", r"\1## Unreleased\n\n", content, count=1, flags=re.DOTALL)
    with open(path, "w") as f:
        f.write(content)
PY

CURRENT_STEP="git commit (dev bump)"
git add -A
git commit -m "chore: bump to $NEXT_DEV

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

CURRENT_STEP="git push (dev bump)"
git push origin main
echo "  ✓ Bumped to $NEXT_DEV"

trap - ERR

echo ""
echo "Done! Release $TAG published."
echo "  GitHub: $(gh release view "$TAG" --json url -q .url)"
