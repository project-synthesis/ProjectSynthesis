"""Tests for the repo index service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.embedding_service import EmbeddingService
from app.services.repo_index_service import (
    IndexStatus,
    RankedFile,
    RepoIndexService,
    _classify_file,
    _extract_outline,
    get_repo_index_service,
)


class TestFileClassification:
    """Test file classification logic."""

    def test_code_files(self):
        assert _classify_file("src/main.py") == "code"
        assert _classify_file("lib/index.ts") == "code"
        assert _classify_file("src/App.svelte") == "code"
        assert _classify_file("backend/main.go") == "code"

    def test_doc_files(self):
        assert _classify_file("README.md") == "doc"
        assert _classify_file("docs/guide.rst") == "doc"

    def test_config_files(self):
        assert _classify_file("package.json") == "config"
        assert _classify_file("docker-compose.yml") == "config"
        assert _classify_file(".env.example") == "config"

    def test_skip_files(self):
        assert _classify_file("package-lock.json") == "skip"
        assert _classify_file("yarn.lock") == "skip"
        assert _classify_file("Cargo.lock") == "skip"
        assert _classify_file("poetry.lock") == "skip"

    def test_unknown_extension(self):
        assert _classify_file("Makefile") == "config"
        assert _classify_file("Dockerfile") == "config"


class TestExtractOutline:
    """Test outline extraction from file content."""

    def test_python_outline(self):
        content = (
            "import os\n"
            "\n"
            "def hello():\n"
            "    pass\n"
            "\n"
            "class MyClass:\n"
            "    def method(self):\n"
            "        pass\n"
            "\n"
            "async def async_func():\n"
            "    pass\n"
        )
        outline = _extract_outline(content)
        assert "def hello():" in outline
        assert "class MyClass:" in outline
        assert "async def async_func():" in outline

    def test_typescript_outline(self):
        content = (
            "export function doStuff() {\n"
            "    return 1;\n"
            "}\n"
            "\n"
            "export class MyService {\n"
            "}\n"
            "\n"
            "export interface Config {\n"
            "}\n"
        )
        outline = _extract_outline(content)
        assert "export function doStuff()" in outline
        assert "export class MyService" in outline
        assert "export interface Config" in outline

    def test_empty_content(self):
        assert _extract_outline("") == ""
        assert _extract_outline("# just a comment\nno code here") == ""

    def test_max_lines_limit(self):
        content = "\n".join(f"def func_{i}():\n    pass" for i in range(200))
        outline = _extract_outline(content, max_lines=10)
        assert outline.count("def func_") == 10


class TestIndexStatus:
    """Test IndexStatus dataclass."""

    def test_is_ready(self):
        assert IndexStatus(status="ready").is_ready is True
        assert IndexStatus(status="partial").is_ready is True
        assert IndexStatus(status="building").is_ready is False
        assert IndexStatus(status="failed").is_ready is False

    def test_is_expired(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        future = datetime.now(timezone.utc) + timedelta(hours=1)

        assert IndexStatus(status="ready", expires_at=past).is_expired is True
        assert IndexStatus(status="ready", expires_at=future).is_expired is False
        assert IndexStatus(status="ready", expires_at=None).is_expired is False


class TestRepoIndexServiceQuery:
    """Test query_relevant_files."""

    @pytest.mark.asyncio
    async def test_query_returns_ranked_results(self):
        """query_relevant_files returns files ranked by similarity."""
        svc = RepoIndexService()

        # Mock DB records
        mock_records = []
        for i, path in enumerate(["src/auth.py", "src/main.py", "README.md"]):
            rec = MagicMock()
            rec.file_path = path
            rec.file_sha = f"sha{i}"
            rec.file_size_bytes = 1000
            rec.outline = f"outline {i}"
            # Create a distinct embedding for each file
            vec = np.zeros(384, dtype=np.float32)
            vec[i] = 1.0
            rec.embedding = vec.tobytes()
            mock_records.append(rec)

        # Mock embedding service
        query_vec = np.zeros(384, dtype=np.float32)
        query_vec[0] = 1.0  # should match src/auth.py

        mock_embed_svc = MagicMock()
        mock_embed_svc.embed_single = AsyncMock(return_value=query_vec)
        # cosine_search is a static method — wire mock to call the real implementation
        mock_embed_svc.cosine_search = EmbeddingService.cosine_search

        with (
            patch(
                "app.services.repo_index_service.async_session"
            ) as mock_session_maker,
            patch(
                "app.services.repo_index_service.get_embedding_service",
                return_value=mock_embed_svc,
            ),
        ):
            # Set up the async session mock
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = mock_records
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_maker.return_value = mock_session

            results = await svc.query_relevant_files("owner/repo", "main", "auth system")

        assert len(results) == 3
        assert isinstance(results[0], RankedFile)
        # First result should be src/auth.py (highest similarity to query)
        assert results[0].path == "src/auth.py"
        assert results[0].score > results[1].score

    @pytest.mark.asyncio
    async def test_query_empty_index(self):
        """query_relevant_files returns empty for non-indexed repo."""
        svc = RepoIndexService()

        with patch(
            "app.services.repo_index_service.async_session"
        ) as mock_session_maker:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_maker.return_value = mock_session

            results = await svc.query_relevant_files("owner/repo", "main", "test")

        assert results == []


class TestSingleton:
    """Test singleton behavior."""

    def test_singleton(self):
        import app.services.repo_index_service as mod
        mod._instance = None
        svc1 = get_repo_index_service()
        svc2 = get_repo_index_service()
        assert svc1 is svc2
        mod._instance = None
