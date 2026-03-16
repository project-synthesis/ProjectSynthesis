# Phase 2: GitHub Integration + MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GitHub OAuth, codebase-aware optimization (embedding + explore), and a 3-tool MCP server — so linked repos provide context and MCP clients can call optimization tools.

**Architecture:** GitHub OAuth stores Fernet-encrypted tokens. Repo linking triggers background embedding index build. Explore phase uses semantic retrieval + single-shot Haiku synthesis. MCP server exposes 3 tools (optimize, prepare, save_result) via streamable HTTP on port 8001. Intent drift gate uses embedding cosine similarity.

**Tech Stack:** Python 3.12+, FastAPI, sentence-transformers (all-MiniLM-L6-v2, 384-dim), cryptography (Fernet), httpx, mcp (fastmcp), numpy

**Spec:** `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md` (Sections 2, 3, 4, 6)

**Phase 1b Handoff:** `docs/superpowers/plans/handoffs/handoff-phase-1b.json` (all_passed: true, 125 tests, 91%)

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `backend/app/services/github_service.py` | Fernet token encryption/decryption |
| `backend/app/services/github_client.py` | Raw GitHub API calls (repos, files, branches) |
| `backend/app/services/embedding_service.py` | Singleton sentence-transformers, batch embed, cosine search |
| `backend/app/services/repo_index_service.py` | Background indexing, SHA staleness, query |
| `backend/app/services/codebase_explorer.py` | Semantic retrieval + single-shot synthesis |
| `backend/app/schemas/mcp_models.py` | MCP tool input/output Pydantic models |
| `backend/app/mcp_server.py` | Standalone MCP server with 3 tools |
| `backend/tests/test_github.py` | OAuth + token encryption tests |
| `backend/tests/test_embedding_service.py` | Embed, cosine search tests |
| `backend/tests/test_repo_index_service.py` | Index build, query tests |
| `backend/tests/test_codebase_explorer.py` | Explore flow tests |
| `backend/tests/test_mcp_tools.py` | MCP tool tests |

### Modify

| File | Changes |
|------|---------|
| `backend/app/routers/github_auth.py` | Replace 501 stubs with real OAuth flow |
| `backend/app/routers/github_repos.py` | Replace 501 stubs with real repo management |
| `backend/app/services/pipeline.py` | Add explore context injection + intent drift gate |
| `backend/app/main.py` | Mount MCP WebSocket endpoint (optional) |
| `backend/requirements.txt` | Add `mcp`, `numpy` |
| `prompts/explore.md` | Real explore synthesis template |
| `prompts/passthrough.md` | Real passthrough combined template |

---

## Chunk 1: GitHub Services + Auth

### Task 1: GitHub Service (Token Encryption)

**Files:**
- Create: `backend/app/services/github_service.py`
- Create: `backend/tests/test_github.py`

- [ ] **Step 1: Write github service tests**

```python
# backend/tests/test_github.py
"""Tests for GitHub token encryption and OAuth flow."""

import pytest
from app.services.github_service import GitHubService


class TestTokenEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        svc = GitHubService(secret_key="test-secret-key-that-is-long-enough-for-fernet-derivation!!")
        token = "gho_abc123xyz"
        encrypted = svc.encrypt_token(token)
        assert encrypted != token.encode()
        decrypted = svc.decrypt_token(encrypted)
        assert decrypted == token

    def test_different_tokens_produce_different_ciphertext(self):
        svc = GitHubService(secret_key="test-secret-key-that-is-long-enough-for-fernet-derivation!!")
        enc1 = svc.encrypt_token("token1")
        enc2 = svc.encrypt_token("token2")
        assert enc1 != enc2

    def test_decrypt_invalid_raises(self):
        svc = GitHubService(secret_key="test-secret-key-that-is-long-enough-for-fernet-derivation!!")
        with pytest.raises(Exception):
            svc.decrypt_token(b"invalid-ciphertext")

    def test_build_oauth_url(self):
        svc = GitHubService(
            secret_key="test-key-long-enough-for-derivation!!",
            client_id="test-client-id",
        )
        url = svc.build_oauth_url(state="random-state")
        assert "client_id=test-client-id" in url
        assert "state=random-state" in url
        assert "github.com" in url
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement github service**

```python
# backend/app/services/github_service.py
"""GitHub token encryption/decryption (Fernet) and OAuth URL building."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class GitHubService:
    """Handles GitHub OAuth token encryption and URL construction."""

    def __init__(
        self,
        secret_key: str,
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        # Derive a Fernet key from the secret
        key = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))
        self._client_id = client_id
        self._client_secret = client_secret

    def encrypt_token(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt_token(self, encrypted: bytes) -> str:
        return self._fernet.decrypt(encrypted).decode()

    def build_oauth_url(self, state: str, scope: str = "repo,read:user") -> str:
        return (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self._client_id}"
            f"&scope={scope}"
            f"&state={state}"
        )
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/github_service.py tests/test_github.py
git commit -m "feat: implement GitHub service with Fernet token encryption"
```

---

### Task 2: GitHub Client (API Calls)

**Files:**
- Create: `backend/app/services/github_client.py`

- [ ] **Step 1: Implement github client**

```python
# backend/app/services/github_client.py
"""Raw GitHub API calls — repo listing, file reads, branch info.

All methods take an explicit `token` parameter — no shared session state.
Uses httpx.AsyncClient for connection pooling.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubClient:
    """Thin wrapper around GitHub REST API."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=30.0)

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def get_user(self, token: str) -> dict:
        """GET /user — validate token and get user info."""
        resp = await self._client.get(f"{GITHUB_API}/user", headers=self._headers(token))
        resp.raise_for_status()
        return resp.json()

    async def list_repos(self, token: str, per_page: int = 30, page: int = 1) -> list[dict]:
        """GET /user/repos — list repos accessible to the user."""
        resp = await self._client.get(
            f"{GITHUB_API}/user/repos",
            headers=self._headers(token),
            params={"per_page": per_page, "page": page, "sort": "updated"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_repo(self, token: str, full_name: str) -> dict:
        resp = await self._client.get(
            f"{GITHUB_API}/repos/{full_name}", headers=self._headers(token),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_branch(self, token: str, full_name: str, branch: str) -> dict:
        resp = await self._client.get(
            f"{GITHUB_API}/repos/{full_name}/branches/{branch}",
            headers=self._headers(token),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_branch_head_sha(self, token: str, full_name: str, branch: str) -> str:
        data = await self.get_branch(token, full_name, branch)
        return data["commit"]["sha"]

    async def get_tree(self, token: str, full_name: str, branch: str) -> list[dict]:
        """GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1"""
        resp = await self._client.get(
            f"{GITHUB_API}/repos/{full_name}/git/trees/{branch}",
            headers=self._headers(token),
            params={"recursive": "1"},
        )
        resp.raise_for_status()
        data = resp.json()
        return [item for item in data.get("tree", []) if item["type"] == "blob"]

    async def get_file_content(self, token: str, full_name: str, path: str, ref: str) -> str | None:
        """GET /repos/{owner}/{repo}/contents/{path}?ref={ref}"""
        resp = await self._client.get(
            f"{GITHUB_API}/repos/{full_name}/contents/{path}",
            headers=self._headers(token),
            params={"ref": ref},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            import base64
            return base64.b64decode(data["content"]).decode(errors="replace")
        return data.get("content", "")
```

- [ ] **Step 2: Commit**

```bash
cd backend && git add app/services/github_client.py
git commit -m "feat: implement GitHub API client"
```

---

### Task 3: GitHub Auth + Repos Routers (Replace Stubs)

**Files:**
- Modify: `backend/app/routers/github_auth.py`
- Modify: `backend/app/routers/github_repos.py`

- [ ] **Step 1: Replace github_auth.py stubs**

The OAuth flow:
1. `GET /api/github/auth/login` → redirect to GitHub OAuth URL with state cookie
2. `GET /api/github/auth/callback` → exchange code for token, encrypt, store in DB
3. `GET /api/github/auth/me` → return current user info
4. `POST /api/github/auth/logout` → delete token from DB

Use session_id from signed cookie. Store encrypted token in `github_tokens` table.

- [ ] **Step 2: Replace github_repos.py stubs**

The repo endpoints:
1. `GET /api/github/repos` → list user's repos via GitHub API
2. `POST /api/github/repos/link` → store linked repo, trigger background index
3. `GET /api/github/repos/linked` → return currently linked repo
4. `DELETE /api/github/repos/unlink` → remove linked repo

- [ ] **Step 3: Run full suite — no regressions**

- [ ] **Step 4: Commit**

```bash
cd backend && git add app/routers/github_auth.py app/routers/github_repos.py
git commit -m "feat: replace GitHub router stubs with real OAuth and repo management"
```

---

## Chunk 2: Embedding + Index + Explorer

### Task 4: Embedding Service

**Files:**
- Create: `backend/app/services/embedding_service.py`
- Create: `backend/tests/test_embedding_service.py`

- [ ] **Step 1: Write embedding service tests**

```python
# backend/tests/test_embedding_service.py
"""Tests for embedding service."""

import pytest
import numpy as np
from app.services.embedding_service import EmbeddingService


class TestEmbeddingService:
    def test_embed_single(self):
        svc = EmbeddingService()
        vec = svc.embed_single("Hello world")
        assert isinstance(vec, np.ndarray)
        assert vec.shape == (384,)  # all-MiniLM-L6-v2 dimension

    def test_embed_texts_batch(self):
        svc = EmbeddingService()
        vecs = svc.embed_texts(["Hello", "World", "Test"])
        assert len(vecs) == 3
        assert all(v.shape == (384,) for v in vecs)

    def test_cosine_search(self):
        svc = EmbeddingService()
        corpus = ["Python function", "JavaScript variable", "Database query"]
        corpus_vecs = svc.embed_texts(corpus)
        query_vec = svc.embed_single("Python code")
        results = svc.cosine_search(query_vec, corpus_vecs, top_k=2)
        # "Python function" should rank higher than "Database query"
        assert len(results) == 2
        assert results[0][0] == 0  # index of "Python function"

    def test_cosine_search_returns_scores(self):
        svc = EmbeddingService()
        corpus_vecs = svc.embed_texts(["exact match test", "unrelated topic"])
        query_vec = svc.embed_single("exact match test")
        results = svc.cosine_search(query_vec, corpus_vecs, top_k=2)
        # First result should have high similarity
        assert results[0][1] > 0.8

    def test_embed_empty_list(self):
        svc = EmbeddingService()
        assert svc.embed_texts([]) == []
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement embedding service**

```python
# backend/app/services/embedding_service.py
"""Singleton sentence-transformers embedding service.

Loads all-MiniLM-L6-v2 (384-dim, CPU) on first use.
Provides batch embed and cosine similarity search.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Singleton embedding service using sentence-transformers."""

    _model = None

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name

    @property
    def model(self):
        if EmbeddingService._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", self._model_name)
            EmbeddingService._model = SentenceTransformer(self._model_name)
        return EmbeddingService._model

    def embed_single(self, text: str) -> np.ndarray:
        return self.model.encode(text, convert_to_numpy=True)

    def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return [embeddings[i] for i in range(len(texts))]

    @staticmethod
    def cosine_search(
        query_vec: np.ndarray,
        corpus_vecs: list[np.ndarray],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """Return top-k (index, score) pairs sorted by cosine similarity."""
        if not corpus_vecs:
            return []
        corpus = np.stack(corpus_vecs)
        # Normalize
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
        corpus_norm = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-9)
        scores = corpus_norm @ query_norm
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in top_indices]
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/embedding_service.py tests/test_embedding_service.py
git commit -m "feat: implement embedding service with sentence-transformers"
```

---

### Task 5: Repo Index Service

**Files:**
- Create: `backend/app/services/repo_index_service.py`
- Create: `backend/tests/test_repo_index_service.py`

- [ ] **Step 1: Write repo index tests**

```python
# backend/tests/test_repo_index_service.py
"""Tests for repo indexing service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.repo_index_service import RepoIndexService
from app.models import RepoIndexMeta, RepoFileIndex


@pytest.fixture
async def index_svc(db_session):
    mock_github = AsyncMock()
    mock_embedding = MagicMock()
    return RepoIndexService(
        db=db_session,
        github_client=mock_github,
        embedding_service=mock_embedding,
    )


class TestRepoIndexService:
    async def test_get_index_status_none(self, index_svc):
        status = await index_svc.get_index_status("owner/repo", "main")
        assert status is None

    async def test_build_index_creates_meta(self, index_svc, db_session):
        index_svc._github.get_tree = AsyncMock(return_value=[
            {"path": "README.md", "type": "blob", "sha": "abc", "size": 100},
        ])
        index_svc._github.get_file_content = AsyncMock(return_value="# Hello")
        index_svc._github.get_branch_head_sha = AsyncMock(return_value="sha123")
        import numpy as np
        index_svc._embedding.embed_texts = MagicMock(return_value=[np.zeros(384)])

        await index_svc.build_index("owner/repo", "main", "fake-token")

        from sqlalchemy import select
        result = await db_session.execute(
            select(RepoIndexMeta).where(RepoIndexMeta.repo_full_name == "owner/repo")
        )
        meta = result.scalar_one()
        assert meta.status == "ready"
        assert meta.head_sha == "sha123"
        assert meta.file_count == 1

    async def test_query_relevant_files(self, index_svc, db_session):
        """Query returns results when index exists."""
        import numpy as np
        # Insert a file index entry
        entry = RepoFileIndex(
            repo_full_name="owner/repo", branch="main",
            file_path="src/main.py", outline="main function",
            embedding=np.zeros(384, dtype=np.float32).tobytes(),
        )
        db_session.add(entry)
        await db_session.commit()

        index_svc._embedding.embed_single = MagicMock(return_value=np.zeros(384))
        index_svc._embedding.cosine_search = MagicMock(return_value=[(0, 0.8)])

        results = await index_svc.query_relevant_files("owner/repo", "main", "test query", top_k=5)
        assert len(results) >= 1

    async def test_is_stale_returns_true_on_sha_mismatch(self, index_svc, db_session):
        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", head_sha="old-sha",
        )
        db_session.add(meta)
        await db_session.commit()

        assert await index_svc.is_stale("owner/repo", "main", "new-sha") is True

    async def test_is_stale_returns_false_on_match(self, index_svc, db_session):
        meta = RepoIndexMeta(
            repo_full_name="owner/repo", branch="main",
            status="ready", head_sha="same-sha",
        )
        db_session.add(meta)
        await db_session.commit()

        assert await index_svc.is_stale("owner/repo", "main", "same-sha") is False
```

- [ ] **Step 2: Run tests — verify they fail**

- [ ] **Step 3: Implement repo index service**

The service should:
- `build_index(repo, branch, token)` — fetch tree, read files, embed, store in `repo_file_index` + update `repo_index_meta`
- `query_relevant_files(repo, branch, query, top_k)` — embed query, cosine search pre-built index
- `get_index_status(repo, branch)` — return meta row or None
- `is_stale(repo, branch, current_sha)` — compare meta.head_sha vs current_sha
- `invalidate_index(repo, branch)` — delete index entries and meta

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/repo_index_service.py tests/test_repo_index_service.py
git commit -m "feat: implement repo index service with background indexing and staleness detection"
```

---

### Task 6: Explore Template + Codebase Explorer

**Files:**
- Modify: `prompts/explore.md` — real content
- Create: `backend/app/services/codebase_explorer.py`
- Create: `backend/tests/test_codebase_explorer.py`

- [ ] **Step 1: Write explore.md template**

```markdown
<user-prompt>
{{raw_prompt}}
</user-prompt>

<file-paths>
{{file_paths}}
</file-paths>

<file-contents>
{{file_contents}}
</file-contents>

## Instructions

You are analyzing a codebase to provide relevant context for prompt optimization.

Given the user's prompt above and the repository files shown, extract:

1. **Relevant patterns** — Code conventions, naming patterns, architecture decisions that relate to the user's prompt
2. **Key files** — Which files are most relevant and why
3. **Technical context** — Framework versions, libraries used, coding style that should inform the optimized prompt
4. **Constraints** — Any project-specific constraints (error handling patterns, type requirements, testing conventions)

Be concise. Focus only on information that would help optimize the user's prompt. Do not describe the entire codebase — only the parts relevant to the prompt's task.

Return a structured summary that can be injected as codebase context into the optimization prompt.
```

- [ ] **Step 2: Write codebase explorer tests**

```python
# backend/tests/test_codebase_explorer.py
"""Tests for codebase explorer."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.codebase_explorer import CodebaseExplorer


@pytest.fixture
def explorer(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "explore.md").write_text(
        "{{raw_prompt}}\n{{file_paths}}\n{{file_contents}}"
    )
    (prompts / "manifest.json").write_text(
        '{"explore.md": {"required": ["raw_prompt", "file_contents", "file_paths"], "optional": []}}'
    )

    mock_github = AsyncMock()
    mock_embedding = MagicMock()
    mock_provider = AsyncMock()
    mock_provider.name = "mock"

    return CodebaseExplorer(
        prompts_dir=prompts,
        github_client=mock_github,
        embedding_service=mock_embedding,
        provider=mock_provider,
    )


class TestCodebaseExplorer:
    async def test_explore_returns_context(self, explorer):
        explorer._github.get_branch_head_sha = AsyncMock(return_value="sha1")
        explorer._github.get_tree = AsyncMock(return_value=[
            {"path": "src/main.py", "type": "blob", "sha": "a1", "size": 100},
        ])
        explorer._github.get_file_content = AsyncMock(return_value="def main(): pass")

        import numpy as np
        explorer._embedding.embed_single = MagicMock(return_value=np.zeros(384))
        explorer._embedding.cosine_search = MagicMock(return_value=[(0, 0.9)])

        from app.schemas.pipeline_contracts import OptimizationResult
        # Mock the provider to return a synthesis result as a string
        explorer._provider.complete_parsed = AsyncMock(return_value="Codebase uses Python with main() entry point.")

        context = await explorer.explore(
            raw_prompt="Write a function",
            repo_full_name="owner/repo",
            branch="main",
            token="fake-token",
        )
        assert context is not None
        assert isinstance(context, str)

    async def test_explore_fallback_on_no_index(self, explorer):
        """When no index exists, falls back to keyword matching."""
        explorer._github.get_branch_head_sha = AsyncMock(return_value="sha1")
        explorer._github.get_tree = AsyncMock(return_value=[
            {"path": "src/main.py", "type": "blob", "sha": "a1", "size": 100},
            {"path": "src/utils.py", "type": "blob", "sha": "a2", "size": 200},
        ])
        explorer._github.get_file_content = AsyncMock(return_value="# code here")
        explorer._embedding.embed_single = MagicMock(side_effect=Exception("Model not loaded"))

        explorer._provider.complete_parsed = AsyncMock(return_value="Fallback context")

        context = await explorer.explore(
            raw_prompt="Write a function",
            repo_full_name="owner/repo",
            branch="main",
            token="fake-token",
        )
        # Should still return context via fallback
        assert context is not None

    async def test_explore_respects_file_limit(self, explorer):
        """Should not read more than EXPLORE_MAX_FILES."""
        files = [{"path": f"file{i}.py", "type": "blob", "sha": f"s{i}", "size": 50}
                 for i in range(100)]
        explorer._github.get_branch_head_sha = AsyncMock(return_value="sha1")
        explorer._github.get_tree = AsyncMock(return_value=files)
        explorer._github.get_file_content = AsyncMock(return_value="code")

        import numpy as np
        explorer._embedding.embed_single = MagicMock(return_value=np.zeros(384))
        explorer._embedding.cosine_search = MagicMock(
            return_value=[(i, 0.9 - i*0.01) for i in range(40)]
        )

        explorer._provider.complete_parsed = AsyncMock(return_value="Context")

        await explorer.explore(
            raw_prompt="Write a function",
            repo_full_name="owner/repo",
            branch="main",
            token="fake-token",
        )
        # Should not read more than EXPLORE_MAX_FILES (40)
        assert explorer._github.get_file_content.call_count <= 40
```

- [ ] **Step 3: Implement codebase explorer**

The explorer:
1. Gets current HEAD SHA
2. Fetches file tree
3. If embedding available: embed prompt → cosine search → top-K files
4. If embedding unavailable: keyword matching on file paths
5. Parallel file reads (capped at `EXPLORE_MAX_FILES`)
6. Renders `explore.md` template with file contents
7. Single-shot synthesis via provider (Haiku 4.5, thinking disabled)
8. Returns synthesis result as string

Key config from `app.config.settings`: `EXPLORE_MAX_FILES` (40), `EXPLORE_TOTAL_LINE_BUDGET` (15000), `EXPLORE_MAX_CONTEXT_CHARS` (700000), `EXPLORE_MAX_PROMPT_CHARS` (20000)

The `complete_parsed` call for synthesis should NOT use output_format (returns raw string, not Pydantic model). Use a separate method or call the provider differently. Since our provider only has `complete_parsed()`, the explorer can define a simple `ExploreResult` model with a single `context` field, or just extract text from the response.

Actually, for Haiku synthesis which returns free-form text, the explorer should call the provider with a simple `ExploreOutput(BaseModel): context: str` model.

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add prompts/explore.md app/services/codebase_explorer.py tests/test_codebase_explorer.py
git commit -m "feat: implement codebase explorer with semantic retrieval and synthesis"
```

---

## Chunk 3: MCP Server + Pipeline Integration

### Task 7: MCP Models + Passthrough Template

**Files:**
- Create: `backend/app/schemas/mcp_models.py`
- Modify: `prompts/passthrough.md`

- [ ] **Step 1: Install mcp dependency**

```bash
cd backend && source .venv/bin/activate && pip install "mcp[cli]" && echo "mcp[cli]" >> requirements.txt
```

Also add `numpy` if not present: `pip install numpy && echo "numpy" >> requirements.txt`

- [ ] **Step 2: Create MCP models**

```python
# backend/app/schemas/mcp_models.py
"""MCP tool input/output Pydantic models."""

from pydantic import BaseModel, ConfigDict, Field


# --- synthesis_optimize ---

class OptimizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(..., min_length=20, max_length=200000)
    strategy: str | None = None
    repo_full_name: str | None = None


class OptimizeOutput(BaseModel):
    optimization_id: str
    optimized_prompt: str
    task_type: str
    strategy_used: str
    changes_summary: str
    scores: dict[str, float]
    original_scores: dict[str, float]
    score_deltas: dict[str, float]
    scoring_mode: str


# --- synthesis_prepare_optimization ---

class PrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(..., min_length=20, max_length=200000)
    strategy: str | None = None
    max_context_tokens: int = Field(128000, ge=4096)
    workspace_path: str | None = None
    repo_full_name: str | None = None


class PrepareOutput(BaseModel):
    trace_id: str
    assembled_prompt: str
    context_size_tokens: int
    strategy_requested: str


# --- synthesis_save_result ---

class SaveResultInput(BaseModel):
    model_config = ConfigDict(extra="ignore")  # lenient
    trace_id: str
    optimized_prompt: str
    changes_summary: str | None = None
    task_type: str | None = None
    strategy_used: str | None = None
    scores: dict[str, float] | None = None
    model: str | None = None


class SaveResultOutput(BaseModel):
    optimization_id: str
    scoring_mode: str
    bias_corrected_scores: dict[str, float]
    strategy_compliance: str
    heuristic_flags: list[str]
```

- [ ] **Step 3: Write passthrough.md template**

```markdown
<user-prompt>
{{raw_prompt}}
</user-prompt>

<codebase-context>
{{codebase_guidance}}
{{codebase_context}}
</codebase-context>

<adaptation>
{{adaptation_state}}
</adaptation>

<strategy>
{{strategy_instructions}}
</strategy>

<scoring-rubric>
{{scoring_rubric_excerpt}}
</scoring-rubric>

## Instructions

You are an expert prompt engineer. Optimize the user's prompt above, then score both the original and your optimized version.

**Optimization guidelines:**
- Preserve the original intent completely
- Add structure, constraints, and specificity
- Remove filler and redundancy
- Apply the strategy above (if provided)

**Scoring guidelines:**
Score both prompts on 5 dimensions (1-10 each):
- **clarity** — How unambiguous is the prompt?
- **specificity** — How many constraints and details?
- **structure** — How well-organized?
- **faithfulness** — Does the optimized preserve intent? (Original always 5.0)
- **conciseness** — Is every word necessary?

Return JSON with: optimized_prompt, changes_summary, task_type, strategy_used, scores: {clarity, specificity, structure, faithfulness, conciseness}
```

- [ ] **Step 4: Commit**

```bash
cd backend && git add app/schemas/mcp_models.py requirements.txt && git add ../../prompts/passthrough.md
git commit -m "feat: add MCP models and passthrough template"
```

---

### Task 8: MCP Server

**Files:**
- Create: `backend/app/mcp_server.py`
- Create: `backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write MCP tool tests**

Test the 3 tools using mocked dependencies. Since MCP tools are async functions, test them by calling the tool functions directly with mocked context.

```python
# backend/tests/test_mcp_tools.py
"""Tests for MCP server tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.mcp_models import OptimizeInput, PrepareInput, SaveResultInput


class TestMCPOptimize:
    async def test_optimize_returns_result(self):
        """synthesis_optimize should run pipeline and return OptimizeOutput."""
        # This tests the tool logic, not the MCP transport
        from app.mcp_server import _run_optimize

        mock_provider = AsyncMock()
        mock_provider.name = "mock"

        result = await _run_optimize(
            prompt="Write a Python function that validates email addresses",
            strategy=None,
            provider=mock_provider,
            db=AsyncMock(),
            prompts_dir=MagicMock(),
        )
        # Should have called the pipeline
        assert mock_provider.complete_parsed.called


class TestMCPPrepare:
    async def test_prepare_returns_assembled_prompt(self):
        from app.mcp_server import _prepare_optimization

        result = await _prepare_optimization(
            prompt="Write a Python function that validates email addresses",
            strategy=None,
            prompts_dir=MagicMock(),
        )
        assert "trace_id" in result
        assert "assembled_prompt" in result


class TestMCPSaveResult:
    async def test_save_applies_bias_correction(self):
        from app.mcp_server import _save_result

        result = await _save_result(
            trace_id="test-trace",
            optimized_prompt="Better prompt",
            scores={"clarity": 9.0, "specificity": 8.0, "structure": 7.0,
                    "faithfulness": 9.0, "conciseness": 8.0},
            db=AsyncMock(),
        )
        # Scores should be bias-corrected (< original)
        assert result["bias_corrected_scores"]["clarity"] < 9.0
```

- [ ] **Step 2: Implement MCP server**

The MCP server:
- Uses `mcp` library (fastmcp)
- Standalone process on port 8001
- 3 tools with `synthesis_` prefix
- Lifespan: detect provider, create httpx client pool
- `synthesis_optimize` runs the full pipeline
- `synthesis_prepare_optimization` assembles prompt + context
- `synthesis_save_result` persists with bias correction

- [ ] **Step 3: Run tests — verify they pass**

- [ ] **Step 4: Commit**

```bash
cd backend && git add app/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: implement MCP server with 3 tools"
```

---

### Task 9: Pipeline Integration (Explore + Intent Drift)

**Files:**
- Modify: `backend/app/services/pipeline.py`

- [ ] **Step 1: Add explore context injection**

In the pipeline's `run()` method, before the analyze phase:
- If `repo_full_name` and `token` are provided, run `CodebaseExplorer.explore()`
- Inject result as `codebase_context` for the optimizer

- [ ] **Step 2: Add intent drift gate**

After scoring, compute embedding cosine similarity between raw_prompt and optimized_prompt:
```python
from app.services.embedding_service import EmbeddingService
embedding_svc = EmbeddingService()
orig_vec = embedding_svc.embed_single(raw_prompt)
opt_vec = embedding_svc.embed_single(optimization.optimized_prompt)
similarity = float(np.dot(orig_vec, opt_vec) / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9))
if similarity < 0.5:
    logger.warning("Intent drift detected: similarity=%.2f trace_id=%s", similarity, trace_id)
```

This is non-blocking — the result is still delivered but with a warning flag.

- [ ] **Step 3: Run full suite — no regressions**

- [ ] **Step 4: Commit**

```bash
cd backend && git add app/services/pipeline.py
git commit -m "feat: add explore context injection and intent drift gate to pipeline"
```

---

### Task 10: Full Coverage + Handoff

**Files:**
- Create: `docs/superpowers/plans/handoffs/handoff-phase-2.json`

- [ ] **Step 1: Run full test suite with coverage**

```bash
cd backend && source .venv/bin/activate && pytest --cov=app --cov-report=term-missing -v
```

- [ ] **Step 2: Generate handoff artifact**

Write `docs/superpowers/plans/handoffs/handoff-phase-2.json` with actual test counts and coverage.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/handoffs/handoff-phase-2.json
git commit -m "docs: write Phase 2 handoff artifact"
```

---

## Exit Conditions Checklist

| # | Condition | Task |
|---|-----------|------|
| 1 | GitHub OAuth flow works | Task 1, 3 |
| 2 | Repo linking triggers background index | Task 3, 5 |
| 3 | codebase_explorer.py produces context | Task 6 |
| 4 | Explore context injected into pipeline | Task 9 |
| 5 | MCP server starts on port 8001 | Task 8 |
| 6 | synthesis_optimize callable | Task 8 |
| 7 | synthesis_prepare + save_result flow | Task 7, 8 |
| 8 | Explore respects Haiku budget (< 200K tokens) | Task 6 |
| 9 | Intent drift gate (cosine < 0.5 warning) | Task 9 |
| 10 | All GitHub/MCP tests pass | Task 10 |
| 11 | handoff-phase-2.json written | Task 10 |
