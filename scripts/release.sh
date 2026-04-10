#!/usr/bin/env bash
# Full release workflow: version sync → changelog extraction → commit → tag → GitHub Release → dev bump.
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
# 1. Determine release version
# ---------------------------------------------------------------------------
CURRENT=$(python3 -c "import json; print(json.load(open('version.json'))['version'])")

if [[ -n "$VERSION_ARG" ]]; then
    RELEASE_VERSION="$VERSION_ARG"
else
    # Strip -dev/-rc suffix from current version
    RELEASE_VERSION=$(echo "$CURRENT" | sed 's/-dev$//' | sed 's/-rc\.[0-9]*$//')
fi

TAG="v$RELEASE_VERSION"

echo "[release] Version: $RELEASE_VERSION (tag: $TAG)"
echo "          Current: $CURRENT"

# ---------------------------------------------------------------------------
# 2. Preflight checks
# ---------------------------------------------------------------------------
if [[ "$(git branch --show-current)" != "main" ]]; then
    echo "  ✗ Must be on main branch (currently on $(git branch --show-current))"
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "  ✗ Working tree is dirty. Commit or stash changes first."
    exit 1
fi

if ! command -v gh &>/dev/null; then
    echo "  ✗ gh CLI not found. Install: https://cli.github.com/"
    exit 1
fi

if git tag -l "$TAG" | grep -q "$TAG"; then
    echo "  ✗ Tag $TAG already exists. Delete it first or choose a different version."
    exit 1
fi

echo "  ✓ Preflight passed"

if $DRY_RUN; then
    echo ""
    echo "[dry-run] Would:"
    echo "  1. Set version.json to $RELEASE_VERSION"
    echo "  2. Sync to backend/_version.py + frontend/package.json"
    echo "  3. Extract changelog for $TAG from docs/CHANGELOG.md"
    echo "  4. Commit: release: $TAG"
    echo "  5. Tag: $TAG"
    echo "  6. Push main + tag"
    echo "  7. Create GitHub Release with changelog body"
    echo "  8. Bump to next dev version"
    exit 0
fi

# ---------------------------------------------------------------------------
# 3. Update version.json to release version (strip -dev)
# ---------------------------------------------------------------------------
echo "{\"version\": \"$RELEASE_VERSION\"}" > version.json
echo "  ✓ version.json → $RELEASE_VERSION"

# ---------------------------------------------------------------------------
# 4. Sync to backend + frontend
# ---------------------------------------------------------------------------
"$SCRIPT_DIR/sync-version.sh"

# ---------------------------------------------------------------------------
# 5. Extract changelog section for the release body
# ---------------------------------------------------------------------------
RELEASE_BODY=$(python3 -c "
import re, sys
with open('docs/CHANGELOG.md') as f:
    content = f.read()

# Look for the release section
pattern = r'## v${RELEASE_VERSION//./\\.} — .*?\n(.*?)(?=\n## v|\Z)'
match = re.search(pattern, content, re.DOTALL)
if match:
    print(match.group(1).strip())
else:
    # If no dated section, try Unreleased
    match = re.search(r'## Unreleased\n(.*?)(?=\n## v|\Z)', content, re.DOTALL)
    if match and match.group(1).strip():
        print(match.group(1).strip())
    else:
        print('Release $RELEASE_VERSION')
")

echo "  ✓ Changelog extracted ($(echo "$RELEASE_BODY" | wc -l) lines)"

# ---------------------------------------------------------------------------
# 6. Commit + tag
# ---------------------------------------------------------------------------
git add -A
git commit -m "release: $TAG

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git tag "$TAG"
echo "  ✓ Committed and tagged $TAG"

# ---------------------------------------------------------------------------
# 7. Push
# ---------------------------------------------------------------------------
git push origin main --tags
echo "  ✓ Pushed to origin"

# ---------------------------------------------------------------------------
# 8. Create GitHub Release
# ---------------------------------------------------------------------------
echo "$RELEASE_BODY" | gh release create "$TAG" \
    --title "$TAG" \
    --notes-file - \
    --latest
echo "  ✓ GitHub Release created: $TAG"

# ---------------------------------------------------------------------------
# 9. Bump to next dev version
# ---------------------------------------------------------------------------
# Increment patch: 0.3.20 → 0.3.21-dev
IFS='.' read -r MAJOR MINOR PATCH <<< "$RELEASE_VERSION"
NEXT_DEV="$MAJOR.$MINOR.$((PATCH + 1))-dev"
echo "{\"version\": \"$NEXT_DEV\"}" > version.json
"$SCRIPT_DIR/sync-version.sh"

git add -A
git commit -m "chore: bump to $NEXT_DEV

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git push origin main
echo "  ✓ Bumped to $NEXT_DEV"

echo ""
echo "Done! Release $TAG published."
echo "  GitHub: $(gh release view "$TAG" --json url -q .url)"
