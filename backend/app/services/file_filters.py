"""Shared file-exclusion filters for repo indexing and explore synthesis.

Single source of truth consumed by:
- ``repo_index_service`` (background GitHub file embedding)
- ``codebase_explorer`` (per-request Haiku synthesis)

Both paths must apply the same exclusions to keep the retrieval budget
focused on files that actually inform prompt optimization.  Drift between
the two caused test files, CI configs, and generated lock files to leak
into the explore context and contribute to the "Explore returned empty
result" failure mode when Haiku's tight CLI-adjusted context ceiling was
exceeded.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Extension whitelist
# ---------------------------------------------------------------------------

INDEXABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini",
    ".html", ".css", ".scss", ".svelte", ".vue",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql",
})

# ---------------------------------------------------------------------------
# Size cap — skip files > 100 KB
# ---------------------------------------------------------------------------

MAX_FILE_SIZE: int = 100_000

# ---------------------------------------------------------------------------
# Test exclusions
# ---------------------------------------------------------------------------

TEST_DIRS: frozenset[str] = frozenset({
    "tests", "test", "__tests__", "spec", "specs",
    "cypress", "playwright", "e2e", "e2e-tests",
    "fixtures", "testdata", "test-data", "test_data",
    "__fixtures__", "__mocks__", "__snapshots__",
})

TEST_SUFFIXES: tuple[str, ...] = (
    "_test", ".test", ".spec", ".stories",
    "_spec", "_bench", "_benchmark",
    ".bench", ".benchmark",
)

# Exact-basename matches for test infrastructure files.  ESM (.mjs) and
# CJS (.cjs) variants included — modern projects commonly use them.
TEST_INFRA: frozenset[str] = frozenset({
    "conftest.py", "testconfig.py", "test_helpers.py",
    "jest.config.js", "jest.config.ts", "jest.config.mjs", "jest.config.cjs",
    "jest.setup.js", "jest.setup.ts",
    "vitest.config.ts", "vitest.config.js", "vitest.config.mjs",
    "vitest.config.cjs", "vitest.setup.ts",
    "playwright.config.ts", "playwright.config.js",
    "playwright.config.mjs", "playwright.config.cjs",
    "cypress.config.ts", "cypress.config.js",
    "cypress.config.mjs", "cypress.config.cjs",
    ".coveragerc", "coverage.config.js",
    "pytest.ini", "setup.cfg", "tox.ini", "noxfile.py",
    "test-setup.ts", "test-setup.js",
})

# ---------------------------------------------------------------------------
# Additional exclusions — low-signal generated/CI files with whitelisted
# extensions that would otherwise slip through.
# ---------------------------------------------------------------------------

# Exact-basename lock files whose extensions are indexable (e.g. .json, .yaml)
_LOCK_FILES: frozenset[str] = frozenset({
    "package-lock.json",
    "pnpm-lock.yaml",
    "npm-shrinkwrap.json",
    "composer.lock",
})

# Path-prefix exclusions — CI/editor/templates folders that add no
# optimization signal.  Checked against the leading path segments.
_EXCLUDED_PATH_PREFIXES: tuple[tuple[str, ...], ...] = (
    (".github", "workflows"),
    (".github", "issue_template"),
    (".github", "pull_request_template"),
    (".vscode",),
    (".idea",),
)


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def is_test_file(path: str) -> bool:
    """Return True for test, spec, bench, and test-infra files.

    Tests duplicate source information while consuming embedding and
    retrieval budget, so they must be excluded from the indexed corpus.
    """
    lower = path.lower()
    segments = lower.split("/")
    basename = segments[-1]

    if any(seg in TEST_DIRS for seg in segments[:-1]):
        return True
    if basename.startswith("test_") or basename.startswith("tests_"):
        return True
    name_no_ext = basename.rsplit(".", 1)[0] if "." in basename else basename
    if any(name_no_ext.endswith(s) or basename.endswith(s) for s in TEST_SUFFIXES):
        return True
    if basename in TEST_INFRA:
        return True
    return False


def _has_excluded_prefix(segments: list[str]) -> bool:
    """Return True if the path starts with any excluded folder sequence."""
    for prefix in _EXCLUDED_PATH_PREFIXES:
        if len(segments) >= len(prefix) and tuple(segments[: len(prefix)]) == prefix:
            return True
    return False


def is_indexable(path: str, size: int | None) -> bool:
    """Single gatekeeper: extension whitelist + size cap + test/CI/lock filters.

    ``size`` is the GitHub tree entry's byte count.  ``None`` means the
    size is unknown — we let it through rather than reject silently.
    """
    if size is not None and size > MAX_FILE_SIZE:
        return False

    dot = path.rfind(".")
    if dot == -1:
        return False
    if path[dot:].lower() not in INDEXABLE_EXTENSIONS:
        return False

    segments = path.lower().split("/")
    basename = segments[-1]

    if basename in _LOCK_FILES:
        return False
    if _has_excluded_prefix(segments):
        return False
    if is_test_file(path):
        return False

    return True
