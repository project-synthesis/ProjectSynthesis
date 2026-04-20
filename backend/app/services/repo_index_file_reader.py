"""File read + embed pipeline shared between full and incremental index builds.

Split from ``repo_index_service.py`` (2026-04-19): both ``build_index``
and ``incremental_update`` need the same pipeline — fetch file contents
with bounded concurrency, extract structured outlines, dedupe via
content SHA against previously-embedded rows, and batch-embed only the
cache misses. Extracting it here keeps ``RepoIndexService`` focused on
tree-diffing and DB writes; the pipeline gets its dependencies
injected so it stays testable without a live service instance.

The file-content TTL cache lives here too because it fences a single
concern (don't re-download the same blob twice within ``_FILE_CACHE_TTL``
seconds across branches/reindex cycles) and the reader is the sole
caller.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RepoFileIndex
from app.services.embedding_service import EmbeddingService
from app.services.github_client import GitHubClient
from app.services.repo_index_outlines import (
    FileOutline,
    build_content_sha,
    build_embedding_text,
    extract_structured_outline,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProcessedFile:
    """A file that has been read, outlined, and embedded — ready for persistence."""
    item: dict  # original tree entry {"path", "sha", "size"}
    content: str
    outline: FileOutline
    embedding: np.ndarray  # 384-dim float32
    content_sha: str = ""  # SHA-256 of embed_text — dedup key


# ---------------------------------------------------------------------------
# File-content cache
# ---------------------------------------------------------------------------
# Module-level TTL + FIFO cache for GitHub file-content fetches, keyed
# by ``(repo_full_name, path, file_sha)``. Git blob SHAs identify
# content exactly, so a cache hit is safe to reuse across branches
# within the same repo. Without a sha we can't verify equivalence, so
# we skip the cache entirely for those calls. Primary win: repeat
# reindex cycles, same-blob files across branches, and vendored-file
# churn. Eviction is strict insertion-order (not LRU — ``get()`` does
# not refresh the timestamp); simpler and equally correct for this
# workload because entries are either reused quickly (same reindex
# run) or drop via TTL.

_file_content_cache: dict[tuple[str, str, str], tuple[float, str]] = {}
_FILE_CACHE_TTL = 900  # 15 minutes — bounds staleness for poll-based reindex
_FILE_CACHE_MAX_ENTRIES = 2048  # hard cap; oldest 10% evicted on overflow


def invalidate_file_cache() -> int:
    """Evict all file-content cache entries. Returns count evicted."""
    count = len(_file_content_cache)
    _file_content_cache.clear()
    return count


def _file_cache_get(key: tuple[str, str, str]) -> str | None:
    """Return cached content if fresh, else ``None`` (evicts stale entry)."""
    entry = _file_content_cache.get(key)
    if entry is None:
        return None
    ts, content = entry
    if time.time() - ts > _FILE_CACHE_TTL:
        _file_content_cache.pop(key, None)
        return None
    return content


def _file_cache_put(key: tuple[str, str, str], content: str) -> None:
    """Insert into cache; evict oldest 10% when over ``_FILE_CACHE_MAX_ENTRIES``."""
    if len(_file_content_cache) >= _FILE_CACHE_MAX_ENTRIES:
        to_evict = max(1, _FILE_CACHE_MAX_ENTRIES // 10)
        oldest = sorted(
            _file_content_cache,
            key=lambda k: _file_content_cache[k][0],
        )[:to_evict]
        for old_key in oldest:
            _file_content_cache.pop(old_key, None)
    _file_content_cache[key] = (time.time(), content)


# ---------------------------------------------------------------------------
# Read + embed pipeline
# ---------------------------------------------------------------------------

async def _read_file(
    gc: GitHubClient,
    semaphore: asyncio.Semaphore,
    token: str,
    repo_full_name: str,
    branch: str,
    item: dict,
) -> tuple[dict, str | None]:
    # TTL-cache lookup keyed on (repo, path, file_sha). Cached blobs
    # are safe to reuse across branches within the same repo — the git
    # blob SHA identifies content exactly. Only keyed when we have a
    # sha; without one we can't guarantee the blob matches.
    path = item["path"]
    sha = item.get("sha")
    cache_key: tuple[str, str, str] | None = (
        (repo_full_name, path, sha) if sha else None
    )
    if cache_key is not None:
        cached = _file_cache_get(cache_key)
        if cached is not None:
            return item, cached

    async with semaphore:
        try:
            content = await gc.get_file_content(
                token, repo_full_name, path, branch,
            )
            if content is not None and cache_key is not None:
                _file_cache_put(cache_key, content)
            return item, content
        except Exception:
            logger.warning(
                "Failed to read %s from %s@%s",
                path, repo_full_name, branch,
            )
            return item, None


async def read_and_embed_files(
    *,
    db: AsyncSession,
    embedding_service: EmbeddingService,
    github_client: GitHubClient,
    items: list[dict],
    token: str,
    repo_full_name: str,
    branch: str,
    concurrency: int = 10,
) -> tuple[list[ProcessedFile], int, int]:
    """Read file content, extract outlines, and batch-embed.

    Shared by ``build_index()`` (full rebuild) and
    ``incremental_update()`` (selective re-embed).

    Returns:
        (processed_files, read_failures, embed_failures)
    """
    if not items:
        return [], 0, 0

    # Phase A: Read files with bounded concurrency
    semaphore = asyncio.Semaphore(concurrency)
    raw: list[tuple[dict, str | None]] = await asyncio.gather(
        *[_read_file(github_client, semaphore, token, repo_full_name, branch, it)
          for it in items]
    )

    # Phase B: Filter out failed reads, extract outlines + embedding text
    #          + compute content_sha for each file (the dedup key).
    read_failures = 0
    valid: list[tuple[dict, str, FileOutline, str, str]] = []  # (item, content, outline, embed_text, content_sha)
    for item, content in raw:
        if content is None:
            read_failures += 1
            continue
        outline = extract_structured_outline(item["path"], content)
        embed_text = build_embedding_text(item["path"], outline)
        # Hash fences on the embedding model — see ``build_content_sha``.
        content_sha = build_content_sha(item["path"], outline)
        valid.append((item, content, outline, embed_text, content_sha))

    if not valid:
        return [], read_failures, 0

    # Phase C: Content-hash dedup — look up cached embeddings for any
    # file whose embed_text SHA-256 already exists in the DB. Shared
    # across branches and repos (a vendored file in two projects gets
    # embedded once). Cache miss → fresh embed; cache hit → byte copy.
    unique_shas = list({cs for _, _, _, _, cs in valid})
    cache: dict[str, np.ndarray] = {}
    if unique_shas:
        cached_rows = (await db.execute(
            select(
                RepoFileIndex.content_sha, RepoFileIndex.embedding,
            ).where(
                RepoFileIndex.content_sha.in_(unique_shas),
                RepoFileIndex.embedding.isnot(None),
            )
        )).all()
        # First hit wins — any row with this SHA has the same embedding
        # by construction (pure function of embed_text).
        for sha, blob in cached_rows:
            if sha not in cache and blob is not None:
                cache[sha] = np.frombuffer(blob, dtype=np.float32).copy()

    # Phase D: Batch embed only the cache-misses.
    miss_indices: list[int] = [
        i for i, (_, _, _, _, cs) in enumerate(valid) if cs not in cache
    ]
    embed_failures = 0
    miss_embeddings: list[np.ndarray] = []
    if miss_indices:
        miss_texts = [valid[i][3] for i in miss_indices]
        try:
            miss_embeddings = await embedding_service.aembed_texts(miss_texts)
        except Exception as exc:
            logger.error(
                "read_and_embed_files: embedding failed for %s@%s (%s) — "
                "persisting %d files with zero vectors",
                repo_full_name, branch, exc, len(miss_indices),
            )
            miss_embeddings = []
            embed_failures = len(miss_indices)

    # Map cache-miss results back to their position in ``valid``.
    miss_vec_by_pos: dict[int, np.ndarray] = {}
    zero_vec = np.zeros(384, dtype=np.float32)
    for slot, pos in enumerate(miss_indices):
        vec = miss_embeddings[slot] if slot < len(miss_embeddings) else zero_vec
        miss_vec_by_pos[pos] = vec.astype(np.float32)

    cache_hits = len(valid) - len(miss_indices)
    if cache_hits:
        logger.info(
            "read_and_embed_files: %s@%s embed_dedup hits=%d misses=%d",
            repo_full_name, branch, cache_hits, len(miss_indices),
        )

    # Phase E: Assemble ProcessedFile results.
    processed: list[ProcessedFile] = []
    for idx, (item, content, outline, _, content_sha) in enumerate(valid):
        # Explicit None check — ``or`` blows up on numpy arrays.
        vec = cache.get(content_sha)
        if vec is None:
            vec = miss_vec_by_pos.get(idx, zero_vec)
        processed.append(ProcessedFile(
            item=item,
            content=content,
            outline=outline,
            embedding=vec.astype(np.float32),
            content_sha=content_sha,
        ))

    return processed, read_failures, embed_failures


__all__ = [
    "ProcessedFile",
    "invalidate_file_cache",
    "read_and_embed_files",
]
