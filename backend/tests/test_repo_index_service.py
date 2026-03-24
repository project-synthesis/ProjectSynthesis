"""Tests for RepoIndexService — background indexing and staleness detection."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.models import RepoFileIndex, RepoIndexMeta
from app.services.repo_index_service import (
    CuratedCodebaseContext,
    RepoIndexService,
    _extract_structured_outline,
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
        content = '"""User authentication service."""\n\nimport logging\nfrom pathlib import Path\n\nclass AuthService:\n    """Handles JWT token creation and validation."""\n\n    def create_token(self, user_id: int) -> str:\n        """Create a new JWT token."""\n        pass\n\n    async def validate_token(self, token: str) -> dict:\n        """Validate and decode a JWT token."""\n        pass\n\ndef helper_function(x: int) -> bool:\n    return x > 0\n'
        outline = _extract_structured_outline("auth.py", content)
        assert outline.file_type == "python"
        assert "AuthService" in outline.structural_summary
        assert "create_token" in outline.structural_summary
        assert "validate_token" in outline.structural_summary
        assert outline.doc_summary is not None
        assert "authentication" in outline.doc_summary.lower()
        assert len(outline.structural_summary) <= 500

    def test_typescript_outline_extracts_exports(self):
        content = '/** API client for backend communication. */\n\nexport interface User {\n  id: string;\n  name: string;\n}\n\nexport async function fetchUser(id: string): Promise<User> {\n  return await fetch(`/api/users/${id}`).then(r => r.json());\n}\n\nexport class ApiClient {\n  constructor(private baseUrl: string) {}\n}\n'
        outline = _extract_structured_outline("client.ts", content)
        assert outline.file_type == "typescript"
        assert "User" in outline.structural_summary
        assert "fetchUser" in outline.structural_summary
        assert "ApiClient" in outline.structural_summary

    def test_markdown_outline_extracts_headings(self):
        content = '# Project Setup\n\n## Installation\n\nFollow these steps to install...\n\n## Configuration\n\nSet the following environment variables...\n\n## Usage\n\nRun the application with...\n'
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
        assert len(outline.structural_summary) <= 500


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
