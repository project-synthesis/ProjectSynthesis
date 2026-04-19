"""Tests for shared file-exclusion filters.

These filters are shared by ``repo_index_service`` (GitHub file embedding)
and ``codebase_explorer`` (per-request Haiku synthesis).  Both paths must
exclude the same low-signal files to keep the retrieval budget focused on
files that actually inform prompt optimization.
"""

from __future__ import annotations

import pytest

from app.services.file_filters import (
    INDEXABLE_EXTENSIONS,
    MAX_FILE_SIZE,
    TEST_DIRS,
    TEST_INFRA,
    TEST_SUFFIXES,
    is_indexable,
    is_test_file,
)


class TestTestDirDetection:
    """Files under test directories are never indexed."""

    @pytest.mark.parametrize("path", [
        "tests/test_foo.py",
        "test/unit/foo.py",
        "frontend/src/__tests__/Foo.test.tsx",
        "spec/models/user_spec.rb",
        "specs/api.spec.ts",
        "cypress/e2e/flow.cy.ts",
        "playwright/tests/basic.ts",
        "e2e/smoke.ts",
        "e2e-tests/basic.ts",
        "fixtures/user.json",
        "testdata/sample.json",
        "test-data/sample.json",
        "test_data/sample.json",
        "__fixtures__/user.json",
        "__mocks__/service.ts",
        "__snapshots__/component.snap",
    ])
    def test_excludes_files_in_test_dirs(self, path: str) -> None:
        assert is_test_file(path) is True


class TestTestSuffixDetection:
    """Files with test/spec/bench suffixes are never indexed."""

    @pytest.mark.parametrize("path", [
        "src/api/user.test.ts",
        "src/api/user.spec.ts",
        "app/models/user_test.py",
        "app/models/user_spec.rb",
        "app/models/user_bench.go",
        "app/models/user_benchmark.go",
        "pkg/api/handler.bench.ts",
        "pkg/api/handler.benchmark.ts",
        "components/Button.stories.tsx",
        "components/Button.stories.mdx",
    ])
    def test_excludes_files_with_test_suffixes(self, path: str) -> None:
        assert is_test_file(path) is True


class TestTestInfraDetection:
    """Test-config files (including ESM/CJS variants) are never indexed."""

    @pytest.mark.parametrize("path", [
        "conftest.py",
        "backend/conftest.py",
        "jest.config.js",
        "jest.config.ts",
        "jest.config.mjs",
        "jest.config.cjs",
        "vitest.config.ts",
        "vitest.config.js",
        "vitest.config.mjs",
        "vitest.config.cjs",
        "playwright.config.ts",
        "playwright.config.mjs",
        "cypress.config.ts",
        "cypress.config.cjs",
        "pytest.ini",
        "tox.ini",
        "noxfile.py",
        ".coveragerc",
    ])
    def test_excludes_test_infra_files(self, path: str) -> None:
        assert is_test_file(path) is True


class TestProductionFilesNotFlagged:
    """Regular source files must never be misclassified as tests."""

    @pytest.mark.parametrize("path", [
        "backend/app/services/pipeline.py",
        "frontend/src/lib/stores/forge.svelte.ts",
        "src/main.py",
        "lib/auth/handler.ts",
        "docs/architecture.md",
        "README.md",
        "package.json",
        "pyproject.toml",
        "backend/app/routers/optimize.py",
        "frontend/src/lib/api/client.ts",
        "src/components/Button.tsx",
    ])
    def test_does_not_flag_production_files(self, path: str) -> None:
        assert is_test_file(path) is False


class TestExtensionWhitelist:
    """Only files with whitelisted extensions are indexable."""

    @pytest.mark.parametrize("path", [
        "src/main.py",
        "src/app.ts",
        "src/app.tsx",
        "src/component.svelte",
        "src/component.vue",
        "pkg/handler.go",
        "src/lib/handler.rs",
        "docs/readme.md",
    ])
    def test_accepts_whitelisted_extensions(self, path: str) -> None:
        assert is_indexable(path, size=1000) is True

    @pytest.mark.parametrize("path", [
        "src/image.png",
        "src/photo.jpg",
        "src/archive.tar.gz",
        "src/binary.exe",
        "src/data.pdf",
        "yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "go.sum",
    ])
    def test_rejects_non_source_extensions(self, path: str) -> None:
        assert is_indexable(path, size=1000) is False

    def test_rejects_files_without_extension(self) -> None:
        assert is_indexable("Makefile", size=1000) is False
        assert is_indexable("Dockerfile", size=1000) is False


class TestSizeCap:
    """Files larger than MAX_FILE_SIZE (100 KB) are rejected."""

    def test_rejects_oversized_file(self) -> None:
        assert is_indexable("src/big.py", size=MAX_FILE_SIZE + 1) is False

    def test_accepts_file_at_size_boundary(self) -> None:
        assert is_indexable("src/edge.py", size=MAX_FILE_SIZE) is True

    def test_treats_missing_size_as_unknown(self) -> None:
        assert is_indexable("src/any.py", size=None) is True


class TestLockFileExclusion:
    """Lock files with indexable extensions (e.g. package-lock.json) must
    not leak into the index when smaller than the size cap."""

    def test_rejects_package_lock_json(self) -> None:
        # package-lock.json is .json (indexable) but is a generated lock file
        assert is_indexable("package-lock.json", size=50_000) is False

    def test_rejects_pnpm_lock_yaml(self) -> None:
        assert is_indexable("pnpm-lock.yaml", size=50_000) is False


class TestCIWorkflowExclusion:
    """GitHub Actions workflow files consume retrieval budget but add no
    prompt-optimization signal."""

    def test_rejects_github_workflow_yaml(self) -> None:
        assert is_indexable(".github/workflows/ci.yaml", size=1000) is False

    def test_rejects_github_issue_template(self) -> None:
        assert is_indexable(".github/ISSUE_TEMPLATE/bug.md", size=1000) is False


class TestIsIndexableCombined:
    """is_indexable() is the single gatekeeper — must combine all rules."""

    def test_rejects_test_file_despite_good_extension(self) -> None:
        assert is_indexable("tests/test_auth.py", size=1000) is False

    def test_rejects_oversized_production_file(self) -> None:
        assert is_indexable("src/main.py", size=MAX_FILE_SIZE + 1) is False

    def test_accepts_regular_production_file(self) -> None:
        assert is_indexable("src/main.py", size=5000) is True


class TestConstantsExposed:
    """Public constants must be exported for visibility."""

    def test_indexable_extensions_is_frozenset(self) -> None:
        assert isinstance(INDEXABLE_EXTENSIONS, frozenset)
        assert ".py" in INDEXABLE_EXTENSIONS
        assert ".ts" in INDEXABLE_EXTENSIONS

    def test_test_dirs_is_frozenset(self) -> None:
        assert isinstance(TEST_DIRS, frozenset)
        assert "tests" in TEST_DIRS
        assert "__tests__" in TEST_DIRS

    def test_test_suffixes_is_tuple(self) -> None:
        assert isinstance(TEST_SUFFIXES, tuple)
        assert ".test" in TEST_SUFFIXES

    def test_test_infra_is_frozenset(self) -> None:
        assert isinstance(TEST_INFRA, frozenset)
        assert "conftest.py" in TEST_INFRA

    def test_max_file_size_is_100kb(self) -> None:
        assert MAX_FILE_SIZE == 100_000
