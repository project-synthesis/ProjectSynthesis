"""Tests for RepoIndexService — background indexing and staleness detection."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.models import RepoFileIndex, RepoIndexMeta
from app.services.repo_index_service import RepoIndexService


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
