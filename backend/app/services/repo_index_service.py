"""Background repo file indexing with SHA-based staleness detection."""

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RepoFileIndex, RepoIndexMeta
from app.services.embedding_service import EmbeddingService
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)

# File extensions that are worth indexing (text/code files)
_INDEXABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini",
    ".html", ".css", ".scss", ".svelte", ".vue",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql",
}
_MAX_FILE_SIZE = 100_000  # bytes — skip files larger than 100 KB


def _is_indexable(path: str, size: int | None) -> bool:
    """Return True if the file is worth indexing."""
    if size is not None and size > _MAX_FILE_SIZE:
        return False
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in _INDEXABLE_EXTENSIONS


def _extract_outline(content: str, max_lines: int = 30) -> str:
    """Return a short outline of the file: first N non-empty lines."""
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return "\n".join(lines[:max_lines])


class RepoIndexService:
    """Manages background indexing of GitHub repo files for semantic search."""

    def __init__(
        self,
        db: AsyncSession,
        github_client: GitHubClient,
        embedding_service: EmbeddingService,
    ) -> None:
        self._db = db
        self._gc = github_client
        self._es = embedding_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_index(
        self,
        repo_full_name: str,
        branch: str,
        token: str,
    ) -> None:
        """Fetch repo tree, read files, embed, and store in DB.

        Updates or creates a RepoIndexMeta row with status="ready", the
        current HEAD SHA, and the file count on success.  Sets
        status="error" if an unrecoverable error occurs.
        """
        logger.info("build_index started for %s@%s", repo_full_name, branch)
        meta = await self._get_or_create_meta(repo_full_name, branch)
        meta.status = "indexing"
        await self._db.flush()

        try:
            head_sha = await self._gc.get_branch_head_sha(token, repo_full_name, branch)
            tree = await self._gc.get_tree(token, repo_full_name, branch)

            indexable = [
                item for item in tree
                if _is_indexable(item["path"], item.get("size"))
            ]

            # Delete existing file index entries for this repo/branch
            await self._db.execute(
                delete(RepoFileIndex).where(
                    RepoFileIndex.repo_full_name == repo_full_name,
                    RepoFileIndex.branch == branch,
                )
            )

            # Read files with bounded concurrency
            semaphore = asyncio.Semaphore(10)
            contents: list[tuple[dict, str | None]] = await asyncio.gather(
                *[self._read_file(semaphore, token, repo_full_name, branch, item)
                  for item in indexable]
            )

            # Batch-embed outlines
            outlines = [
                _extract_outline(content) if content else ""
                for _, content in contents
            ]
            texts_to_embed = [o if o.strip() else item["path"]
                              for (item, _), o in zip(contents, outlines)]

            embeddings: list[np.ndarray] = []
            if texts_to_embed:
                embeddings = self._es.embed_texts(texts_to_embed)

            # Persist file index rows
            file_count = 0
            for idx, (item, content) in enumerate(contents):
                if content is None:
                    continue
                vec: np.ndarray = embeddings[idx] if idx < len(embeddings) else np.zeros(384, dtype=np.float32)
                row = RepoFileIndex(
                    repo_full_name=repo_full_name,
                    branch=branch,
                    file_path=item["path"],
                    file_sha=item.get("sha"),
                    file_size_bytes=item.get("size"),
                    outline=outlines[idx],
                    embedding=vec.astype(np.float32).tobytes(),
                )
                self._db.add(row)
                file_count += 1

            # Update meta
            meta.status = "ready"
            meta.head_sha = head_sha
            meta.file_count = file_count
            meta.error_message = None
            meta.indexed_at = datetime.now(timezone.utc)
            await self._db.commit()

            logger.info(
                "build_index complete for %s@%s: %d files indexed",
                repo_full_name, branch, file_count,
            )

        except Exception as exc:
            logger.exception("build_index failed for %s@%s", repo_full_name, branch)
            meta.status = "error"
            meta.error_message = str(exc)
            await self._db.commit()
            raise

    async def query_relevant_files(
        self,
        repo_full_name: str,
        branch: str,
        query: str,
        top_k: int = 10,
    ) -> list[dict]:
        """Embed query and cosine-search the pre-built index.

        Returns a list of dicts with keys: file_path, outline, score.
        """
        result = await self._db.execute(
            select(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
                RepoFileIndex.embedding.isnot(None),
            )
        )
        rows = result.scalars().all()
        if not rows:
            return []

        query_vec: np.ndarray = self._es.embed_single(query)

        corpus_vecs = [
            np.frombuffer(row.embedding, dtype=np.float32) for row in rows
        ]

        ranked: list[tuple[int, float]] = self._es.cosine_search(
            query_vec, corpus_vecs, top_k=min(top_k, len(rows))
        )

        return [
            {
                "file_path": rows[idx].file_path,
                "outline": rows[idx].outline,
                "score": score,
            }
            for idx, score in ranked
        ]

    async def get_index_status(
        self, repo_full_name: str, branch: str
    ) -> RepoIndexMeta | None:
        """Return the RepoIndexMeta for this repo/branch, or None if absent."""
        result = await self._db.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == repo_full_name,
                RepoIndexMeta.branch == branch,
            )
        )
        return result.scalars().first()

    async def is_stale(
        self, repo_full_name: str, branch: str, current_sha: str
    ) -> bool:
        """Return True if the stored head_sha differs from current_sha."""
        meta = await self.get_index_status(repo_full_name, branch)
        if meta is None:
            return True
        if meta.head_sha is None:
            return False  # Legacy row — no SHA to compare; treat as fresh
        return meta.head_sha != current_sha

    async def invalidate_index(self, repo_full_name: str, branch: str) -> None:
        """Delete all index entries and the meta row for this repo/branch."""
        await self._db.execute(
            delete(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
            )
        )
        await self._db.execute(
            delete(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == repo_full_name,
                RepoIndexMeta.branch == branch,
            )
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_or_create_meta(
        self, repo_full_name: str, branch: str
    ) -> RepoIndexMeta:
        meta = await self.get_index_status(repo_full_name, branch)
        if meta is None:
            meta = RepoIndexMeta(
                repo_full_name=repo_full_name,
                branch=branch,
                status="pending",
                file_count=0,
            )
            self._db.add(meta)
            await self._db.flush()
        return meta

    async def _read_file(
        self,
        semaphore: asyncio.Semaphore,
        token: str,
        repo_full_name: str,
        branch: str,
        item: dict,
    ) -> tuple[dict, str | None]:
        async with semaphore:
            try:
                content = await self._gc.get_file_content(
                    token, repo_full_name, item["path"], branch
                )
                return item, content
            except Exception:
                logger.warning(
                    "Failed to read %s from %s@%s",
                    item["path"], repo_full_name, branch,
                )
                return item, None
