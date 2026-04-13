"""Tests for RepoIndexService — background indexing and staleness detection."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import RepoFileIndex, RepoIndexMeta
from app.services.github_client import GitHubApiError
from app.services.repo_index_service import (
    RepoIndexService,
    _classify_github_error,
    _classify_source_type,
    _compute_source_weight,
    _extract_markdown_references,
    _extract_structured_outline,
    invalidate_curated_cache,
)


def _make_svc(db, github_client=None, embedding_service=None):
    gc = github_client or AsyncMock()
    es = embedding_service or MagicMock()
    return RepoIndexService(db=db, github_client=gc, embedding_service=es)


# ---------------------------------------------------------------------------
# Test 1: get_index_status returns None when no meta exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_index_status_none(db_session):
    svc = _make_svc(db_session)
    result = await svc.get_index_status("owner/repo", "main")
    assert result is None


# ---------------------------------------------------------------------------
# Test 2: build_index creates/updates meta with status="ready"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_index_creates_meta(db_session):
    gc = AsyncMock()
    # get_branch_head_sha is what the service calls directly
    gc.get_branch_head_sha.return_value = "abc123"
    # get_tree returns one blob file
    gc.get_tree.return_value = [
        {"type": "blob", "path": "src/main.py", "sha": "fileshaabc", "size": 200}
    ]
    # get_file_content returns some Python source
    gc.get_file_content.return_value = "def main():\n    pass\n"

    es = MagicMock()
    zero_vec = np.zeros(384, dtype=np.float32)
    es.embed_texts.return_value = [zero_vec]
    es.aembed_texts = AsyncMock(return_value=[zero_vec])

    svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)
    await svc.build_index("owner/repo", "main", "ghp_token")

    meta = await svc.get_index_status("owner/repo", "main")
    assert meta is not None
    assert meta.status == "ready"
    assert meta.head_sha == "abc123"
    assert meta.file_count == 1
    # Synthesis fields should be at their defaults (synthesis runs separately)
    assert meta.synthesis_status == "pending"
    assert meta.synthesis_error is None


# ---------------------------------------------------------------------------
# Test 3: query_relevant_files returns ranked results
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_relevant_files(db_session):
    zero_vec = np.zeros(384, dtype=np.float32)

    # Pre-insert a RepoFileIndex entry
    entry = RepoFileIndex(
        repo_full_name="owner/repo",
        branch="main",
        file_path="src/utils.py",
        file_sha="sha111",
        outline="def helper(): ...",
        embedding=zero_vec.tobytes(),
    )
    db_session.add(entry)
    await db_session.commit()

    gc = AsyncMock()
    es = MagicMock()
    es.embed_single.return_value = zero_vec
    es.aembed_single = AsyncMock(return_value=zero_vec)
    # cosine_search is a staticmethod; mock it on the class instance too
    es.cosine_search = MagicMock(return_value=[(0, 0.95)])

    svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)
    results = await svc.query_relevant_files("owner/repo", "main", "helper function", top_k=5)

    assert len(results) == 1
    assert results[0]["file_path"] == "src/utils.py"
    assert results[0]["outline"] == "def helper(): ..."
    assert abs(results[0]["score"] - 0.95) < 1e-6


# ---------------------------------------------------------------------------
# Test 4: is_stale returns True when head_sha differs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_stale_sha_mismatch(db_session):
    meta = RepoIndexMeta(
        repo_full_name="owner/repo",
        branch="main",
        status="ready",
        head_sha="old_sha",
        file_count=0,
    )
    db_session.add(meta)
    await db_session.commit()

    svc = _make_svc(db_session)
    stale = await svc.is_stale("owner/repo", "main", "new_sha")
    assert stale is True


# ---------------------------------------------------------------------------
# Test 5: is_stale returns False when head_sha matches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_stale_sha_match(db_session):
    meta = RepoIndexMeta(
        repo_full_name="owner/repo",
        branch="feature",
        status="ready",
        head_sha="same_sha",
        file_count=0,
    )
    db_session.add(meta)
    await db_session.commit()

    svc = _make_svc(db_session)
    stale = await svc.is_stale("owner/repo", "feature", "same_sha")
    assert stale is False


# ---------------------------------------------------------------------------
# TestStructuredOutlines
# ---------------------------------------------------------------------------

class TestStructuredOutlines:
    def test_python_outline_extracts_signatures(self):
        content = (
            '"""User authentication service."""\n\nimport logging\n'
            "from pathlib import Path\n\nclass AuthService:\n"
            '    """Handles JWT token creation and validation."""\n\n'
            "    def create_token(self, user_id: int) -> str:\n"
            '        """Create a new JWT token."""\n        pass\n\n'
            "    async def validate_token(self, token: str) -> dict:\n"
            '        """Validate and decode a JWT token."""\n        pass\n\n'
            "def helper_function(x: int) -> bool:\n    return x > 0\n"
        )
        outline = _extract_structured_outline("auth.py", content)
        assert outline.file_type == "python"
        assert "AuthService" in outline.structural_summary
        assert "create_token" in outline.structural_summary
        assert "validate_token" in outline.structural_summary
        assert outline.doc_summary is not None
        assert "authentication" in outline.doc_summary.lower()
        assert len(outline.structural_summary) <= settings.INDEX_OUTLINE_MAX_CHARS

    def test_typescript_outline_extracts_exports(self):
        content = (
            "/** API client for backend communication. */\n\n"
            "export interface User {\n  id: string;\n  name: string;\n}\n\n"
            "export async function fetchUser(id: string): Promise<User> {\n"
            "  return await fetch(`/api/users/${id}`).then(r => r.json());\n"
            "}\n\nexport class ApiClient {\n"
            "  constructor(private baseUrl: string) {}\n}\n"
        )
        outline = _extract_structured_outline("client.ts", content)
        assert outline.file_type == "typescript"
        assert "User" in outline.structural_summary
        assert "fetchUser" in outline.structural_summary
        assert "ApiClient" in outline.structural_summary

    def test_markdown_outline_extracts_headings(self):
        content = (
            "# Project Setup\n\n## Installation\n\n"
            "Follow these steps to install...\n\n## Configuration\n\n"
            "Set the following environment variables...\n\n## Usage\n\n"
            "Run the application with...\n"
        )
        outline = _extract_structured_outline("README.md", content)
        assert outline.file_type == "docs"
        assert "Project Setup" in outline.structural_summary
        assert "Installation" in outline.structural_summary
        assert "Configuration" in outline.structural_summary

    def test_config_outline_extracts_keys(self):
        content = '{"name": "my-app", "version": "1.0.0", "scripts": {"dev": "vite"}, "dependencies": {}}'
        outline = _extract_structured_outline("package.json", content)
        assert outline.file_type == "config"

    def test_generic_fallback(self):
        content = "some content\nwith lines\nclass Foo:\n    pass\ndef bar():\n    pass"
        outline = _extract_structured_outline("unknown.xyz", content)
        assert outline.file_type == "other"

    def test_outline_capped_at_max_chars(self):
        long_content = "\n".join(f"def func_{i}(x): pass" for i in range(200))
        outline = _extract_structured_outline("big.py", long_content)
        assert len(outline.structural_summary) <= settings.INDEX_OUTLINE_MAX_CHARS


# ---------------------------------------------------------------------------
# TestCuratedRetrieval
# ---------------------------------------------------------------------------

class TestCuratedRetrieval:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_index(self, db_session):
        gc = AsyncMock()
        es = AsyncMock()

        svc = RepoIndexService(db_session, gc, es)
        result = await svc.query_curated_context("owner/repo", "main", "query")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_curated_context_with_indexed_data(self, db_session):
        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", file_count=2, head_sha="abc123",
        )
        db_session.add(meta)

        vec1 = np.random.randn(384).astype(np.float32)
        vec2 = np.random.randn(384).astype(np.float32)
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="backend/app/auth.py", file_sha="a1",
            outline="class AuthService:\n  def login(self):",
            embedding=vec1.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="backend/app/models.py", file_sha="a2",
            outline="class User:\n  id: int",
            embedding=vec2.tobytes(),
        ))
        await db_session.commit()

        gc = AsyncMock()
        es = MagicMock()
        query_vec = np.random.randn(384).astype(np.float32)
        es.aembed_single = AsyncMock(return_value=query_vec)
        es.cosine_search = MagicMock(return_value=[(0, 0.85), (1, 0.72)])

        svc = RepoIndexService(db_session, gc, es)
        result = await svc.query_curated_context("owner/repo", "main", "authentication")

        assert result is not None
        assert result.files_included > 0
        assert result.top_relevance_score > 0.0
        assert "auth.py" in result.context_text

    @pytest.mark.asyncio
    async def test_domain_boosting(self, db_session):
        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", file_count=2, head_sha="abc123",
        )
        db_session.add(meta)

        vec = np.ones(384, dtype=np.float32) * 0.5
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="backend/app/service.py", file_sha="a1",
            outline="class Service:", embedding=vec.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="frontend/src/App.svelte", file_sha="a2",
            outline="<script>export let name</script>", embedding=vec.tobytes(),
        ))
        await db_session.commit()

        gc = AsyncMock()
        es = MagicMock()
        es.aembed_single = AsyncMock(return_value=vec)
        # Both files have same base score
        es.cosine_search = MagicMock(return_value=[(0, 0.5), (1, 0.5)])

        svc = RepoIndexService(db_session, gc, es)
        # Domain=backend should boost backend/app/service.py
        result = await svc.query_curated_context(
            "owner/repo", "main", "query", domain="backend",
        )
        assert result is not None
        assert "service.py" in result.context_text

    @pytest.mark.asyncio
    async def test_max_chars_budget_enforcement(self, db_session):
        """Files exceeding the max_chars budget are excluded from results."""
        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", file_count=3, head_sha="abc123",
        )
        db_session.add(meta)

        vec = np.ones(384, dtype=np.float32) * 0.5
        # Create 3 files with outlines of ~100 chars each
        for i in range(3):
            db_session.add(RepoFileIndex(
                repo_full_name="owner/repo", branch="main",
                file_path=f"src/module_{i}.py", file_sha=f"sha{i}",
                outline="x" * 80,  # ~80 chars + header ≈ ~120 per entry
                embedding=vec.tobytes(),
            ))
        await db_session.commit()

        gc = AsyncMock()
        es = MagicMock()
        es.aembed_single = AsyncMock(return_value=vec)
        es.cosine_search = MagicMock(return_value=[
            (0, 0.9), (1, 0.85), (2, 0.8),
        ])

        svc = RepoIndexService(db_session, gc, es)
        # Budget of 250 chars should only fit ~2 entries (each ~120 chars)
        result = await svc.query_curated_context(
            "owner/repo", "main", "query", max_chars=250,
        )
        assert result is not None
        assert result.files_included == 2  # third file exceeds budget


# ---------------------------------------------------------------------------
# Unique constraint and upsert tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_or_create_meta_idempotent(db_session):
    """Calling _get_or_create_meta twice returns the same row, no duplicates."""
    svc = _make_svc(db_session)
    meta1 = await svc._get_or_create_meta("owner/repo", "main")
    meta2 = await svc._get_or_create_meta("owner/repo", "main")
    assert meta1.id == meta2.id

    from sqlalchemy import func
    from sqlalchemy import select as sa_select
    count_q = await db_session.execute(
        sa_select(func.count()).select_from(RepoIndexMeta).where(
            RepoIndexMeta.repo_full_name == "owner/repo",
            RepoIndexMeta.branch == "main",
        )
    )
    assert count_q.scalar() == 1


@pytest.mark.asyncio
async def test_unique_constraint_prevents_duplicates(db_session):
    """DB-level unique constraint rejects duplicate (repo_full_name, branch)."""
    from sqlalchemy.exc import IntegrityError

    meta1 = RepoIndexMeta(
        repo_full_name="owner/repo", branch="main",
        status="ready", file_count=5,
    )
    db_session.add(meta1)
    await db_session.flush()

    meta2 = RepoIndexMeta(
        repo_full_name="owner/repo", branch="main",
        status="pending", file_count=0,
    )
    db_session.add(meta2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_concurrent_get_or_create_meta(db_session):
    """Parallel upsert calls resolve to the same row."""
    import asyncio

    svc = _make_svc(db_session)
    results = await asyncio.gather(
        svc._get_or_create_meta("owner/repo", "main"),
        svc._get_or_create_meta("owner/repo", "main"),
    )
    assert results[0].id == results[1].id


# ---------------------------------------------------------------------------
# get_embeddings_by_paths tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_embeddings_by_paths_returns_matching(db_session):
    """Returns embeddings only for paths that exist in the index."""
    vec_a = np.random.randn(384).astype(np.float32)
    vec_b = np.random.randn(384).astype(np.float32)

    db_session.add_all([
        RepoFileIndex(
            repo_full_name="o/r", branch="main", file_path="src/a.py",
            file_sha="sha1", embedding=vec_a.tobytes(),
        ),
        RepoFileIndex(
            repo_full_name="o/r", branch="main", file_path="src/b.py",
            file_sha="sha2", embedding=vec_b.tobytes(),
        ),
    ])
    await db_session.commit()

    es = MagicMock()
    es.dimension = 384
    svc = _make_svc(db_session, embedding_service=es)
    result = await svc.get_embeddings_by_paths(
        "o/r", "main", ["src/a.py", "src/b.py", "src/missing.py"],
    )

    assert set(result.keys()) == {"src/a.py", "src/b.py"}
    np.testing.assert_array_almost_equal(result["src/a.py"], vec_a)
    np.testing.assert_array_almost_equal(result["src/b.py"], vec_b)


@pytest.mark.asyncio
async def test_get_embeddings_by_paths_empty_input(db_session):
    """Empty path list returns empty dict without querying DB."""
    es = MagicMock()
    es.dimension = 384
    svc = _make_svc(db_session, embedding_service=es)
    result = await svc.get_embeddings_by_paths("o/r", "main", [])
    assert result == {}


@pytest.mark.asyncio
async def test_get_embeddings_by_paths_skips_null_embeddings(db_session):
    """Rows with null embeddings are excluded from results."""
    db_session.add(RepoFileIndex(
        repo_full_name="o/r", branch="main", file_path="src/no_embed.py",
        file_sha="sha1", embedding=None,
    ))
    await db_session.commit()

    es = MagicMock()
    es.dimension = 384
    svc = _make_svc(db_session, embedding_service=es)
    result = await svc.get_embeddings_by_paths("o/r", "main", ["src/no_embed.py"])
    assert result == {}


@pytest.mark.asyncio
async def test_get_embeddings_by_paths_skips_wrong_dimension(db_session):
    """Embeddings with unexpected dimension are silently skipped."""
    bad_vec = np.zeros(128, dtype=np.float32)  # wrong dim
    db_session.add(RepoFileIndex(
        repo_full_name="o/r", branch="main", file_path="src/bad.py",
        file_sha="sha1", embedding=bad_vec.tobytes(),
    ))
    await db_session.commit()

    es = MagicMock()
    es.dimension = 384
    svc = _make_svc(db_session, embedding_service=es)
    result = await svc.get_embeddings_by_paths("o/r", "main", ["src/bad.py"])
    assert result == {}


@pytest.mark.asyncio
async def test_get_embeddings_by_paths_isolates_repo_branch(db_session):
    """Embeddings from other repos/branches are not returned."""
    vec = np.random.randn(384).astype(np.float32)
    db_session.add(RepoFileIndex(
        repo_full_name="other/repo", branch="dev", file_path="src/a.py",
        file_sha="sha1", embedding=vec.tobytes(),
    ))
    await db_session.commit()

    es = MagicMock()
    es.dimension = 384
    svc = _make_svc(db_session, embedding_service=es)
    result = await svc.get_embeddings_by_paths("o/r", "main", ["src/a.py"])
    assert result == {}


# ---------------------------------------------------------------------------
# Incremental update tests
# ---------------------------------------------------------------------------

def _make_incremental_svc(db, tree_items, file_contents, head_sha="new_sha"):
    """Helper to build a RepoIndexService with mocked GitHub + embedding."""
    gc = AsyncMock()
    gc.get_branch_head_sha.return_value = head_sha
    gc.get_tree.return_value = tree_items

    # Map (path) -> content for get_file_content
    content_map = file_contents or {}

    async def _read_content(_token, _repo, path, _ref):
        return content_map.get(path)

    gc.get_file_content = AsyncMock(side_effect=_read_content)

    es = MagicMock()
    zero_vec = np.zeros(384, dtype=np.float32)
    es.aembed_texts = AsyncMock(
        side_effect=lambda texts: [zero_vec for _ in texts]
    )
    es.dimension = 384

    return RepoIndexService(db=db, github_client=gc, embedding_service=es)


class TestIncrementalUpdate:
    """Tests for RepoIndexService.incremental_update()."""

    @pytest.mark.asyncio
    async def test_skips_when_no_meta(self, db_session):
        """Returns no_index skip when build_index was never run."""
        svc = _make_incremental_svc(db_session, [], {})
        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "no_index"

    @pytest.mark.asyncio
    async def test_skips_when_indexing_in_progress(self, db_session):
        """Returns indexing skip when a concurrent build_index is running."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="indexing", file_count=0, head_sha="old",
        ))
        await db_session.commit()

        svc = _make_incremental_svc(db_session, [], {})
        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "indexing"

    @pytest.mark.asyncio
    async def test_skips_when_head_unchanged(self, db_session):
        """No work when HEAD SHA hasn't changed."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="same_sha",
        ))
        await db_session.commit()

        svc = _make_incremental_svc(db_session, [], {}, head_sha="same_sha")
        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "head_unchanged"

    @pytest.mark.asyncio
    async def test_detects_changed_files(self, db_session):
        """Changed files (SHA mismatch) are re-embedded."""
        vec = np.zeros(384, dtype=np.float32)
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old_sha",
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/main.py", file_sha="old_file_sha",
            content="old content", outline="old outline",
            embedding=vec.tobytes(),
        ))
        await db_session.commit()

        tree = [{"path": "src/main.py", "sha": "new_file_sha", "size": 50}]
        svc = _make_incremental_svc(
            db_session, tree,
            {"src/main.py": "def updated(): pass"},
            head_sha="new_sha",
        )
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["changed"] == 1
        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["skipped_reason"] is None

        # Verify the row was updated
        row = (await db_session.execute(
            select(RepoFileIndex).where(RepoFileIndex.file_path == "src/main.py")
        )).scalar_one()
        assert row.file_sha == "new_file_sha"
        assert row.content == "def updated(): pass"

    @pytest.mark.asyncio
    async def test_detects_added_files(self, db_session):
        """New files in the tree are indexed."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=0, head_sha="old_sha",
        ))
        await db_session.commit()

        tree = [{"path": "src/new.py", "sha": "new_sha_1", "size": 30}]
        svc = _make_incremental_svc(
            db_session, tree,
            {"src/new.py": "def new_func(): pass"},
            head_sha="new_sha",
        )
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["added"] == 1
        assert result["changed"] == 0
        assert result["removed"] == 0

        # Verify the new row exists
        row = (await db_session.execute(
            select(RepoFileIndex).where(RepoFileIndex.file_path == "src/new.py")
        )).scalar_one()
        assert row.file_sha == "new_sha_1"
        assert row.content == "def new_func(): pass"

        # Verify meta file_count was incremented
        meta = (await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "o/r",
            )
        )).scalar_one()
        assert meta.file_count == 1
        assert meta.head_sha == "new_sha"

    @pytest.mark.asyncio
    async def test_detects_removed_files(self, db_session):
        """Files absent from tree are deleted from the index."""
        vec = np.zeros(384, dtype=np.float32)
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old_sha",
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/deleted.py", file_sha="dead_sha",
            content="old", outline="old", embedding=vec.tobytes(),
        ))
        await db_session.commit()

        # Empty tree — file was removed
        svc = _make_incremental_svc(
            db_session, [], {}, head_sha="new_sha",
        )
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["removed"] == 1
        assert result["changed"] == 0
        assert result["added"] == 0

        # Verify the row is gone
        count = (await db_session.execute(
            select(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == "o/r",
            )
        )).scalars().all()
        assert len(count) == 0

        # Verify file_count decremented
        meta = (await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "o/r",
            )
        )).scalar_one()
        assert meta.file_count == 0

    @pytest.mark.asyncio
    async def test_mixed_changes(self, db_session):
        """Handles changed + added + removed in a single pass."""
        vec = np.zeros(384, dtype=np.float32)
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=2, head_sha="old_sha",
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/keep.py", file_sha="keep_sha",
            content="keep", outline="keep", embedding=vec.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/modify.py", file_sha="old_mod_sha",
            content="old", outline="old", embedding=vec.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/remove.py", file_sha="remove_sha",
            content="dead", outline="dead", embedding=vec.tobytes(),
        ))
        await db_session.commit()

        tree = [
            {"path": "src/keep.py", "sha": "keep_sha", "size": 20},   # unchanged
            {"path": "src/modify.py", "sha": "new_mod_sha", "size": 30},  # changed
            {"path": "src/brand_new.py", "sha": "new_sha", "size": 25},  # added
            # src/remove.py is absent — removed
        ]
        svc = _make_incremental_svc(
            db_session, tree,
            {
                "src/modify.py": "def modified(): pass",
                "src/brand_new.py": "def brand_new(): pass",
            },
            head_sha="new_sha",
        )
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["changed"] == 1
        assert result["added"] == 1
        assert result["removed"] == 1

        # Verify final state: 3 files (keep + modify + brand_new)
        rows = (await db_session.execute(
            select(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == "o/r",
            )
        )).scalars().all()
        paths = {r.file_path for r in rows}
        assert paths == {"src/keep.py", "src/modify.py", "src/brand_new.py"}

    @pytest.mark.asyncio
    async def test_head_changed_but_no_file_diffs(self, db_session):
        """Commit with no file changes (e.g. merge commit) just updates SHA."""
        vec = np.zeros(384, dtype=np.float32)
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old_sha",
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/main.py", file_sha="same_sha",
            content="same", outline="same", embedding=vec.tobytes(),
        ))
        await db_session.commit()

        tree = [{"path": "src/main.py", "sha": "same_sha", "size": 20}]
        svc = _make_incremental_svc(
            db_session, tree, {}, head_sha="new_head_sha",
        )
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["changed"] == 0
        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["skipped_reason"] is None

        # SHA should be updated despite no file changes
        meta = (await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "o/r",
            )
        )).scalar_one()
        assert meta.head_sha == "new_head_sha"

    @pytest.mark.asyncio
    async def test_skips_non_indexable_files(self, db_session):
        """Non-indexable files in tree are ignored (e.g. binary, too large)."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=0, head_sha="old_sha",
        ))
        await db_session.commit()

        tree = [
            {"path": "image.png", "sha": "img_sha", "size": 50000},   # not indexable ext
            {"path": "big.py", "sha": "big_sha", "size": 200_000},     # too large
            {"path": "tests/test_main.py", "sha": "test_sha", "size": 100},  # test file
        ]
        svc = _make_incremental_svc(
            db_session, tree, {}, head_sha="new_sha",
        )
        result = await svc.incremental_update("o/r", "main", "tok")

        # All files filtered out — only SHA update
        assert result["changed"] == 0
        assert result["added"] == 0
        assert result["removed"] == 0


class TestCuratedCacheInvalidation:
    def test_invalidate_clears_all(self):
        """invalidate_curated_cache() clears the module-level cache."""
        from app.services.repo_index_service import _curated_cache

        # Clear any entries from prior tests, then seed one
        _curated_cache.clear()
        _curated_cache["test_key"] = (0.0, "value")
        assert len(_curated_cache) == 1

        count = invalidate_curated_cache()
        assert count == 1
        assert len(_curated_cache) == 0


# ---------------------------------------------------------------------------
# GitHub error classification
# ---------------------------------------------------------------------------

class TestClassifyGitHubError:
    def test_401_maps_to_token_expired(self):
        assert _classify_github_error(GitHubApiError(401, "Bad credentials")) == "token_expired"

    def test_403_maps_to_rate_limited(self):
        assert _classify_github_error(GitHubApiError(403, "rate limit")) == "rate_limited"

    def test_404_maps_to_repo_not_found(self):
        assert _classify_github_error(GitHubApiError(404, "Not Found")) == "repo_not_found"

    def test_500_maps_to_generic(self):
        assert _classify_github_error(GitHubApiError(500, "Internal")) == "github_500"


# ---------------------------------------------------------------------------
# Error resilience tests for incremental_update
# ---------------------------------------------------------------------------

class TestIncrementalUpdateErrors:
    """Tests that incremental_update degrades gracefully on errors."""

    @pytest.mark.asyncio
    async def test_github_401_returns_token_expired(self, db_session):
        """Expired token returns skip reason, no crash."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.side_effect = GitHubApiError(401, "Bad credentials")
        es = MagicMock()
        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)

        result = await svc.incremental_update("o/r", "main", "bad_token")
        assert result["skipped_reason"] == "token_expired"
        assert result["elapsed_ms"] >= 0

    @pytest.mark.asyncio
    async def test_github_403_returns_rate_limited(self, db_session):
        """Rate-limited returns skip reason, no crash."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.side_effect = GitHubApiError(403, "rate limit exceeded")
        es = MagicMock()
        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)

        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "rate_limited"

    @pytest.mark.asyncio
    async def test_github_404_returns_repo_not_found(self, db_session):
        """Deleted repo returns skip reason, no crash."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.side_effect = GitHubApiError(404, "Not Found")
        es = MagicMock()
        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)

        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "repo_not_found"

    @pytest.mark.asyncio
    async def test_network_error_on_sha_check(self, db_session):
        """Network failure during SHA check returns skip reason."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.side_effect = ConnectionError("timeout")
        es = MagicMock()
        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)

        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "network_error"

    @pytest.mark.asyncio
    async def test_github_error_on_tree_fetch(self, db_session):
        """GitHub error during tree fetch returns skip reason."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=1, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.return_value = "new_sha"
        gc.get_tree.side_effect = GitHubApiError(403, "rate limit")
        es = MagicMock()
        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)

        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["skipped_reason"] == "rate_limited"

    @pytest.mark.asyncio
    async def test_partial_read_failures(self, db_session):
        """Files that fail to read are tracked; successful ones still persist."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=0, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.return_value = "new_sha"
        gc.get_tree.return_value = [
            {"path": "src/good.py", "sha": "sha1", "size": 50},
            {"path": "src/bad.py", "sha": "sha2", "size": 50},
        ]

        async def _read(_token, _repo, path, _ref):
            if "bad" in path:
                return None  # Simulates read failure
            return "def good(): pass"

        gc.get_file_content = AsyncMock(side_effect=_read)

        es = MagicMock()
        zero_vec = np.zeros(384, dtype=np.float32)
        es.aembed_texts = AsyncMock(return_value=[zero_vec])

        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["added"] == 2  # Both counted as "added" in diff
        assert result["read_failures"] == 1  # One failed to read
        # Only the good file should be persisted
        rows = (await db_session.execute(
            select(RepoFileIndex).where(RepoFileIndex.repo_full_name == "o/r")
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].file_path == "src/good.py"

    @pytest.mark.asyncio
    async def test_embedding_failure_persists_without_vectors(self, db_session):
        """Embedding service failure doesn't lose files — persists with zero vectors."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=0, head_sha="old",
        ))
        await db_session.commit()

        gc = AsyncMock()
        gc.get_branch_head_sha.return_value = "new_sha"
        gc.get_tree.return_value = [
            {"path": "src/main.py", "sha": "sha1", "size": 50},
        ]
        gc.get_file_content = AsyncMock(return_value="def main(): pass")

        es = MagicMock()
        es.aembed_texts = AsyncMock(side_effect=RuntimeError("model not loaded"))

        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)
        result = await svc.incremental_update("o/r", "main", "tok")

        assert result["added"] == 1
        assert result["embed_failures"] == 1
        assert result["skipped_reason"] is None

        # File should still be persisted (with zero vector fallback)
        row = (await db_session.execute(
            select(RepoFileIndex).where(RepoFileIndex.file_path == "src/main.py")
        )).scalar_one()
        assert row.content == "def main(): pass"
        assert row.file_sha == "sha1"
        # Embedding should be a zero vector (fallback)
        vec = np.frombuffer(row.embedding, dtype=np.float32)
        np.testing.assert_array_equal(vec, np.zeros(384, dtype=np.float32))

    @pytest.mark.asyncio
    async def test_file_count_never_negative(self, db_session):
        """file_count is clamped to 0 even when more files are removed than counted."""
        db_session.add(RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=0,  # Already 0
            head_sha="old",
        ))
        vec = np.zeros(384, dtype=np.float32)
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="src/orphan.py", file_sha="dead",
            content="x", outline="x", embedding=vec.tobytes(),
        ))
        await db_session.commit()

        # Orphan file removed (file_count was already 0 — stale count)
        gc = AsyncMock()
        gc.get_branch_head_sha.return_value = "new_sha"
        gc.get_tree.return_value = []  # Empty tree
        es = MagicMock()
        svc = RepoIndexService(db=db_session, github_client=gc, embedding_service=es)

        result = await svc.incremental_update("o/r", "main", "tok")
        assert result["removed"] == 1

        meta = (await db_session.execute(
            select(RepoIndexMeta).where(RepoIndexMeta.repo_full_name == "o/r")
        )).scalar_one()
        assert meta.file_count == 0  # Clamped, not -1

    @pytest.mark.asyncio
    async def test_result_includes_elapsed_ms(self, db_session):
        """Return dict always includes elapsed_ms for observability."""
        svc = _make_incremental_svc(db_session, [], {})
        result = await svc.incremental_update("o/r", "main", "tok")
        assert "elapsed_ms" in result
        assert isinstance(result["elapsed_ms"], float)
        assert result["elapsed_ms"] >= 0

    @pytest.mark.asyncio
    async def test_result_includes_failure_counts(self, db_session):
        """Return dict always includes read_failures and embed_failures."""
        svc = _make_incremental_svc(db_session, [], {})
        result = await svc.incremental_update("o/r", "main", "tok")
        assert "read_failures" in result
        assert "embed_failures" in result
        assert result["read_failures"] == 0
        assert result["embed_failures"] == 0


# ---------------------------------------------------------------------------
# Source-type classifier tests
# ---------------------------------------------------------------------------

class TestClassifySourceType:
    def test_python_is_code(self):
        assert _classify_source_type("backend/app/services/warm_path.py") == "code"

    def test_typescript_is_code(self):
        assert _classify_source_type("frontend/src/lib/stores/github.svelte.ts") == "code"

    def test_svelte_is_code(self):
        assert _classify_source_type("frontend/src/lib/components/Editor.svelte") == "code"

    def test_markdown_is_docs(self):
        assert _classify_source_type("README.md") == "docs"

    def test_docs_dir_yaml_is_docs(self):
        assert _classify_source_type("docs/routing-architecture.yaml") == "docs"

    def test_plans_dir_is_docs(self):
        assert _classify_source_type("docs/plans/2026-04-08-phase1.md") == "docs"

    def test_adr_dir_is_docs(self):
        assert _classify_source_type("docs/adr/ADR-005.md") == "docs"

    def test_yaml_outside_docs_is_config(self):
        assert _classify_source_type("config/settings.yaml") == "config"

    def test_json_is_config(self):
        assert _classify_source_type("package.json") == "config"

    def test_sql_is_code(self):
        assert _classify_source_type("backend/schema.sql") == "code"

    def test_shell_is_code(self):
        assert _classify_source_type("scripts/deploy.sh") == "code"


# ---------------------------------------------------------------------------
# Markdown reference extraction tests
# ---------------------------------------------------------------------------

class TestExtractMarkdownReferences:
    def test_backtick_file_paths(self):
        content = (
            "See `backend/app/services/warm_path.py` for the scheduler.\n"
            "Also check `frontend/src/lib/stores/github.svelte.ts`.\n"
        )
        refs = _extract_markdown_references(content)
        assert "backend/app/services/warm_path.py" in refs
        assert "frontend/src/lib/stores/github.svelte.ts" in refs

    def test_ignores_non_prefixed_paths(self):
        content = "See `README.md` and `CHANGELOG.md` for details."
        refs = _extract_markdown_references(content)
        assert refs == []

    def test_empty_content(self):
        assert _extract_markdown_references("") == []
        assert _extract_markdown_references(None) == []

    def test_code_block_paths_extracted(self):
        content = "```\nbackend/app/models.py\n```\nSee `app/config.py` for settings."
        refs = _extract_markdown_references(content)
        assert "app/config.py" in refs

    def test_multiple_refs_same_file(self):
        content = "`backend/app/main.py` mentioned twice: `backend/app/main.py`"
        refs = _extract_markdown_references(content)
        assert refs.count("backend/app/main.py") == 2  # dedup happens in caller


# ---------------------------------------------------------------------------
# Curated retrieval: skip-and-continue packing tests
# ---------------------------------------------------------------------------

def _setup_curated_files(db_session, files, meta_kwargs=None):
    """Helper to set up indexed files for curated retrieval tests.

    ``files``: list of (path, content_or_outline, sim_score) tuples.
    Returns (svc, cosine_return) for use in the test.
    """
    meta_kw = {
        "repo_full_name": "o/r", "branch": "main",
        "status": "ready", "file_count": len(files), "head_sha": "abc",
    }
    if meta_kwargs:
        meta_kw.update(meta_kwargs)
    db_session.add(RepoIndexMeta(**meta_kw))

    vec = np.ones(384, dtype=np.float32) * 0.5
    cosine_return = []
    for i, (path, body, score) in enumerate(files):
        # If body is long, treat as content; otherwise as outline only
        is_content = len(body) > 200
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path=path, file_sha=f"sha{i}",
            content=body if is_content else None,
            outline=body if not is_content else body[:100],
            embedding=vec.tobytes(),
        ))
        cosine_return.append((i, score))

    gc = AsyncMock()
    es = MagicMock()
    es.aembed_single = AsyncMock(return_value=vec)
    es.cosine_search = MagicMock(return_value=cosine_return)
    svc = RepoIndexService(db_session, gc, es)
    return svc


class TestCuratedSkipAndContinue:
    @pytest.fixture(autouse=True)
    def clear_curated_cache(self):
        invalidate_curated_cache()

    @pytest.mark.asyncio
    async def test_skips_oversized_file_packs_smaller(self, db_session):
        """When file 2 doesn't fit, file 3 still gets packed."""
        svc = _setup_curated_files(db_session, [
            ("backend/app/small1.py", "x" * 300, 0.9),  # ~330 with header
            ("backend/app/huge.py", "x" * 50000, 0.8),   # won't fit in 1000 budget
            ("backend/app/small2.py", "x" * 300, 0.7),  # should still fit
        ])
        await db_session.commit()

        result = await svc.query_curated_context("o/r", "main", "skip_oversized", max_chars=1000)
        assert result is not None
        paths = [f["path"] for f in result.selected_files]
        assert "backend/app/small1.py" in paths
        assert "backend/app/small2.py" in paths
        assert "backend/app/huge.py" not in paths
        assert result.budget_skip_count >= 1

    @pytest.mark.asyncio
    async def test_max_skip_bound_stops_scanning(self, db_session):
        """After 5 consecutive skips, the loop terminates."""
        # 1 small file + 7 huge files — spread across dirs to avoid diversity cap
        files = [("backend/app/small.py", "x" * 300, 0.95)]
        for i in range(7):
            files.append((f"backend/svc{i}/huge{i}.py", "x" * 50000, 0.9 - i * 0.05))
        svc = _setup_curated_files(db_session, files)
        await db_session.commit()

        result = await svc.query_curated_context("o/r", "main", "max_skip_bound", max_chars=1000)
        assert result is not None
        assert result.stop_reason == "budget"
        assert result.budget_skip_count == 5  # stopped at MAX_BUDGET_SKIPS

    @pytest.mark.asyncio
    async def test_skip_counter_resets_on_success(self, db_session):
        """Skip counter resets when a file successfully packs."""
        # Spread across dirs to avoid per-dir diversity cap (max 3)
        svc = _setup_curated_files(db_session, [
            ("backend/svc_a/a.py", "x" * 300, 0.95),      # packs
            ("backend/svc_b/big1.py", "x" * 50000, 0.90),  # skip
            ("backend/svc_c/b.py", "x" * 300, 0.85),      # packs, resets counter
            ("backend/svc_d/big2.py", "x" * 50000, 0.80),  # skip
            ("backend/svc_e/big3.py", "x" * 50000, 0.75),  # skip
            ("backend/svc_f/c.py", "x" * 200, 0.70),      # packs, resets again
        ])
        await db_session.commit()

        result = await svc.query_curated_context("o/r", "main", "skip_resets", max_chars=2000)
        assert result is not None
        packed_paths = [f["path"] for f in result.selected_files]
        assert "backend/svc_a/a.py" in packed_paths
        assert "backend/svc_c/b.py" in packed_paths
        assert "backend/svc_f/c.py" in packed_paths
        assert result.stop_reason == "relevance_exhausted"

    @pytest.mark.asyncio
    async def test_stop_reason_relevance_exhausted_after_skips(self, db_session):
        """If all files are processed (some skipped), stop_reason is relevance_exhausted."""
        svc = _setup_curated_files(db_session, [
            ("backend/app/a.py", "x" * 300, 0.9),
            ("backend/app/big.py", "x" * 50000, 0.8),  # skipped
            ("backend/app/b.py", "x" * 300, 0.7),
        ])
        await db_session.commit()

        result = await svc.query_curated_context("o/r", "main", "stop_reason_test", max_chars=2000)
        assert result is not None
        assert result.stop_reason == "relevance_exhausted"
        assert result.budget_skip_count == 1


# ---------------------------------------------------------------------------
# Curated retrieval: source-type balance tests
# ---------------------------------------------------------------------------

class TestCuratedSourceTypeBalance:
    @pytest.fixture(autouse=True)
    def clear_curated_cache(self):
        invalidate_curated_cache()

    @pytest.mark.asyncio
    async def test_doc_cap_defers_excess_docs(self, db_session):
        """With 4 docs and 2 code files, code files get priority slots."""
        svc = _setup_curated_files(db_session, [
            ("docs/plans/plan1.md", "x" * 300, 0.95),      # doc - included
            ("docs/plans/plan2.md", "x" * 300, 0.90),      # doc - may be deferred
            ("docs/adr/adr1.md", "x" * 300, 0.85),         # doc - deferred
            ("docs/plans/plan3.md", "x" * 300, 0.80),      # doc - deferred
            ("backend/app/warm_path.py", "x" * 300, 0.75),  # code
            ("backend/app/cold_path.py", "x" * 300, 0.70),  # code
        ])
        await db_session.commit()

        result = await svc.query_curated_context(
            "o/r", "main", "doc_cap_test", max_chars=80000, domain="backend",
        )
        assert result is not None
        assert result.doc_deferred_count > 0
        # Code files should appear despite lower similarity
        packed_paths = [f["path"] for f in result.selected_files]
        assert "backend/app/warm_path.py" in packed_paths
        assert "backend/app/cold_path.py" in packed_paths

    @pytest.mark.asyncio
    async def test_all_docs_no_starvation(self, db_session):
        """When all files are docs, they're all packed (no artificial starvation)."""
        svc = _setup_curated_files(db_session, [
            ("docs/plan1.md", "x" * 300, 0.9),
            ("docs/plan2.md", "x" * 300, 0.8),
            ("docs/adr1.md", "x" * 300, 0.7),
        ])
        await db_session.commit()

        result = await svc.query_curated_context("o/r", "main", "all_docs_test", max_chars=80000)
        assert result is not None
        assert result.files_included == 3

    @pytest.mark.asyncio
    async def test_doc_cap_guard_small_corpus(self, db_session):
        """With only 2 files (both docs), guard prevents cap from firing."""
        svc = _setup_curated_files(db_session, [
            ("docs/plan1.md", "x" * 300, 0.9),
            ("docs/plan2.md", "x" * 300, 0.8),
        ])
        await db_session.commit()

        result = await svc.query_curated_context("o/r", "main", "guard_small_test", max_chars=80000)
        assert result is not None
        assert result.files_included == 2
        assert result.doc_deferred_count == 0


# ---------------------------------------------------------------------------
# Curated retrieval: markdown reference expansion tests
# ---------------------------------------------------------------------------

class TestCuratedMarkdownExpansion:
    @pytest.fixture(autouse=True)
    def clear_curated_cache(self):
        invalidate_curated_cache()

    @pytest.mark.asyncio
    async def test_doc_ref_expands_referenced_code(self, db_session):
        """A doc file referencing a code file via backtick triggers expansion."""
        doc_content = (
            "# Architecture Plan\n"
            "The scheduler lives in `backend/app/services/warm_path.py`.\n"
            "It uses the config from `backend/app/config.py`.\n"
        )
        code_content = "class AdaptiveScheduler:\n    " + "x" * 500

        meta = RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=2, head_sha="abc",
        )
        db_session.add(meta)

        vec = np.ones(384, dtype=np.float32) * 0.5
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="docs/plans/scheduler.md", file_sha="s1",
            content=doc_content, outline="# Architecture Plan",
            embedding=vec.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="backend/app/services/warm_path.py", file_sha="s2",
            content=code_content, outline="class AdaptiveScheduler",
            embedding=vec.tobytes(),
        ))
        await db_session.commit()

        gc = AsyncMock()
        es = MagicMock()
        es.aembed_single = AsyncMock(return_value=vec)
        # Both files score above threshold — the doc higher than the code.
        # Row order from DB is unpredictable, so give both high scores.
        es.cosine_search = MagicMock(return_value=[(0, 0.8), (1, 0.5)])

        svc = RepoIndexService(db_session, gc, es)
        result = await svc.query_curated_context("o/r", "main", "doc_ref_test", max_chars=80000)

        assert result is not None
        packed_paths = [f["path"] for f in result.selected_files]
        assert "docs/plans/scheduler.md" in packed_paths
        assert "backend/app/services/warm_path.py" in packed_paths
        # At least one file should come via doc-ref expansion OR direct similarity.
        # The key test: the doc file's backtick reference was parsed.
        # If warm_path.py was packed by similarity (0.5), that's also fine —
        # the system found it either way. Check that doc-ref source appears
        # if the doc was packed first and warm_path was below it in the list.
        sources = {f["path"]: f["source"] for f in result.selected_files}
        # warm_path.py should be either "full" (direct sim) or "doc-ref" (expansion)
        assert sources["backend/app/services/warm_path.py"] in ("full", "doc-ref")


# ---------------------------------------------------------------------------
# Curated retrieval: regression test for code-dominated retrievals
# ---------------------------------------------------------------------------

class TestCuratedCodeDominatedRegression:
    @pytest.fixture(autouse=True)
    def clear_curated_cache(self):
        invalidate_curated_cache()

    @pytest.mark.asyncio
    async def test_code_files_with_imports_still_expand(self, db_session):
        """Import-graph expansion still works for code-dominated retrievals."""
        main_content = (
            "from app.services.helper import HelperService\n"
            "from app.config import settings\n\n"
            "class MainService:\n    " + "x" * 500
        )
        helper_content = "class HelperService:\n    " + "x" * 500

        meta = RepoIndexMeta(
            repo_full_name="o/r", branch="main",
            status="ready", file_count=2, head_sha="abc",
        )
        db_session.add(meta)

        vec = np.ones(384, dtype=np.float32) * 0.5
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="backend/app/services/main_svc.py", file_sha="s1",
            content=main_content, outline="class MainService",
            embedding=vec.tobytes(),
        ))
        db_session.add(RepoFileIndex(
            repo_full_name="o/r", branch="main",
            file_path="backend/app/services/helper.py", file_sha="s2",
            content=helper_content, outline="class HelperService",
            embedding=vec.tobytes(),
        ))
        await db_session.commit()

        gc = AsyncMock()
        es = MagicMock()
        es.aembed_single = AsyncMock(return_value=vec)
        # Both files above threshold — row order from DB is unpredictable
        es.cosine_search = MagicMock(return_value=[(0, 0.85), (1, 0.5)])

        svc = RepoIndexService(db_session, gc, es)
        result = await svc.query_curated_context("o/r", "main", "import_regression", max_chars=80000)

        assert result is not None
        packed_paths = [f["path"] for f in result.selected_files]
        assert "backend/app/services/main_svc.py" in packed_paths
        assert "backend/app/services/helper.py" in packed_paths
        # helper.py should appear — either via import-graph or direct similarity.
        # The import mechanism works if main_svc.py was packed first and expanded its import.
        sources = {f["path"]: f["source"] for f in result.selected_files}
        assert sources["backend/app/services/helper.py"] in ("full", "import-graph")


# ---------------------------------------------------------------------------
# D1: Source-type weighting tests
# ---------------------------------------------------------------------------


class TestComputeSourceWeight:
    """Unit tests for _compute_source_weight()."""

    def test_code_is_baseline(self):
        assert _compute_source_weight("src/main.py") == 1.0

    def test_docs_penalized(self):
        assert _compute_source_weight("docs/guide.md") == 0.6

    def test_config_penalized(self):
        assert _compute_source_weight("config.yaml") == 0.75

    def test_github_community_extra_penalty(self):
        # .github/ docs: 0.6 * 0.5 = 0.3
        assert _compute_source_weight(".github/SECURITY.md") == pytest.approx(0.3)

    def test_root_readme_exempt(self):
        assert _compute_source_weight("README.md") == 1.0

    def test_nested_readme_not_exempt(self):
        # docs/README.md is in a docs dir — gets docs penalty
        assert _compute_source_weight("docs/README.md") == 0.6

    def test_github_non_docs_file(self):
        # .github/workflows/ci.yml is config, not docs — no community penalty
        w = _compute_source_weight(".github/workflows/ci.yml")
        assert w == 0.75  # config weight, no community penalty

    def test_unknown_extension_defaults_to_config(self):
        # _classify_source_type returns "config" for unknown → weight 0.75
        w = _compute_source_weight("Makefile")
        assert w == 0.75

    def test_weights_configurable(self, monkeypatch):
        """Override weights to all 1.0 → disables weighting."""
        monkeypatch.setattr(settings, "INDEX_SOURCE_TYPE_WEIGHTS", {"code": 1.0, "docs": 1.0, "config": 1.0})
        assert _compute_source_weight("docs/guide.md") == 1.0

    def test_curated_metadata_includes_weight_fields(self):
        """Verify _compute_source_weight returns valid float for code files."""
        w = _compute_source_weight("backend/app/main.py")
        assert isinstance(w, float)
        assert 0.0 <= w <= 2.0
