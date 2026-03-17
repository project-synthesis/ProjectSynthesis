#!/usr/bin/env bash
# Reads version.json and propagates to all version consumers.
# Usage: ./scripts/sync-version.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."

VERSION=$(python3 -c "import json; print(json.load(open('$ROOT/version.json'))['version'])")

if [[ -z "$VERSION" ]]; then
    echo "ERROR: Could not read version from version.json"
    exit 1
fi

echo "Syncing version: $VERSION"

# 1. backend/app/_version.py
echo "__version__ = \"$VERSION\"" > "$ROOT/backend/app/_version.py"
echo "  ✓ backend/app/_version.py"

# 2. frontend/package.json (strip -dev/-rc suffixes for npm semver compliance)
NPM_VERSION=$(echo "$VERSION" | sed 's/-dev$//' | sed 's/-rc\.[0-9]*$//')
cd "$ROOT/frontend"
npm version "$NPM_VERSION" --no-git-tag-version --allow-same-version >/dev/null 2>&1
echo "  ✓ frontend/package.json → $NPM_VERSION"

echo "Done. Version $VERSION propagated to all consumers."
echo ""
echo "Remaining manual steps:"
echo "  1. Update docs/CHANGELOG.md (move items from Unreleased to v$VERSION)"
echo "  2. Commit: git add -A && git commit -m \"release: v$VERSION\""
echo "  3. Tag: git tag v$VERSION && git push origin main --tags"
