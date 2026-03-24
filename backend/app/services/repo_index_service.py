"""Background repo file indexing with SHA-based staleness detection."""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FileOutline:
    file_path: str
    file_type: str
    structural_summary: str
    imports_summary: str | None = None
    doc_summary: str | None = None
    size_lines: int = 0
    size_bytes: int = 0


@dataclass
class CuratedCodebaseContext:
    context_text: str
    files_included: int
    total_files_indexed: int
    index_freshness: str
    top_relevance_score: float


# ---------------------------------------------------------------------------
# Structured outline extraction
# ---------------------------------------------------------------------------

def _extract_structured_outline(path: str, content: str) -> FileOutline:
    """Extract a structured outline from file content based on file type."""
    ext = path[path.rfind("."):].lower() if "." in path else ""
    lines = content.splitlines()
    size_lines = len(lines)
    size_bytes = len(content.encode("utf-8", errors="replace"))
    max_chars = settings.INDEX_OUTLINE_MAX_CHARS

    extractor = _OUTLINE_EXTRACTORS.get(ext, _extract_generic_outline)
    outline = extractor(path, content, lines)
    outline.size_lines = size_lines
    outline.size_bytes = size_bytes

    # Enforce max chars
    if len(outline.structural_summary) > max_chars:
        outline.structural_summary = outline.structural_summary[:max_chars]
    return outline


def _extract_python_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    doc = _extract_docstring(lines)
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*(class |(?:async )?def )\w+", ln)
    ][:15]
    return FileOutline(
        file_path=path, file_type="python",
        structural_summary="\n".join(sigs),
        doc_summary=doc,
    )


def _extract_typescript_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    doc = None
    if content.startswith("/**"):
        end = content.find("*/")
        if end != -1:
            doc = content[3:end].strip().split("\n")[0].strip(" *")
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(
            r"^export\s+(interface|type|function|async function|class|const)\s+\w+",
            ln,
        )
    ][:15]
    return FileOutline(
        file_path=path, file_type="typescript",
        structural_summary="\n".join(sigs),
        doc_summary=doc,
    )


def _extract_markdown_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    headings = [
        ln.rstrip() for ln in lines if re.match(r"^#{1,2}\s+", ln)
    ][:10]
    first_para = ""
    for ln in lines:
        if ln.strip() and not ln.startswith("#"):
            first_para = ln.strip()
            break
    summary = "\n".join(headings)
    return FileOutline(
        file_path=path, file_type="docs",
        structural_summary=summary,
        doc_summary=first_para[:200] if first_para else None,
    )


def _extract_config_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    preview = "\n".join(lines[:15])
    return FileOutline(
        file_path=path, file_type="config",
        structural_summary=preview,
    )


def _extract_sql_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    stmts = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(CREATE\s+(TABLE|INDEX|FUNCTION|VIEW))", ln, re.IGNORECASE)
    ][:15]
    return FileOutline(
        file_path=path, file_type="sql",
        structural_summary="\n".join(stmts),
    )


def _extract_svelte_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    exports = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*export\s+(let|const|function)\s+", ln)
    ][:10]
    component_name = path.rsplit("/", 1)[-1].replace(".svelte", "")
    summary = f"Component: {component_name}\n" + "\n".join(exports)
    return FileOutline(
        file_path=path, file_type="svelte",
        structural_summary=summary,
    )


def _extract_generic_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(class |def |function |export )", ln)
    ][:15]
    if not sigs:
        non_empty = [ln for ln in lines if ln.strip()][:20]
        sigs = non_empty
    return FileOutline(
        file_path=path, file_type="other",
        structural_summary="\n".join(sigs),
    )


def _extract_docstring(lines: list[str]) -> str | None:
    """Extract first paragraph of a Python module docstring."""
    in_doc = False
    doc_lines: list[str] = []
    for ln in lines[:30]:
        stripped = ln.strip()
        if not in_doc and stripped.startswith('"""'):
            in_doc = True
            content = stripped[3:]
            if content.endswith('"""') and len(content) > 3:
                return content[:-3].strip()
            if content:
                doc_lines.append(content)
            continue
        if in_doc:
            if '"""' in stripped:
                before = stripped.split('"""')[0].strip()
                if before:
                    doc_lines.append(before)
                break
            if stripped == "" and doc_lines:
                break  # First paragraph only
            doc_lines.append(stripped)
    return " ".join(doc_lines).strip() or None


_OUTLINE_EXTRACTORS: dict[str, Callable[[str, str, list[str]], FileOutline]] = {
    ".py": _extract_python_outline,
    ".ts": _extract_typescript_outline,
    ".js": _extract_typescript_outline,
    ".tsx": _extract_typescript_outline,
    ".jsx": _extract_typescript_outline,
    ".svelte": _extract_svelte_outline,
    ".json": _extract_config_outline,
    ".yaml": _extract_config_outline,
    ".yml": _extract_config_outline,
    ".toml": _extract_config_outline,
    ".md": _extract_markdown_outline,
    ".sql": _extract_sql_outline,
}


# ---------------------------------------------------------------------------
# Module-level helpers for curated retrieval
# ---------------------------------------------------------------------------

def _build_embedding_text(path: str, outline: FileOutline) -> str:
    """Combine path + structural info for richer embedding."""
    parts = [path]
    if outline.doc_summary:
        parts.append(outline.doc_summary)
    if outline.structural_summary:
        parts.append(outline.structural_summary)
    return " | ".join(parts)[:1000]


_DOMAIN_PATH_PATTERNS: dict[str, list[str]] = {
    "backend": ["backend/", "server/", "api/", "app/"],
    "frontend": ["frontend/", "src/components/", "src/lib/"],
    "database": ["models/", "migrations/", "alembic/"],
    "devops": ["docker", "ci/", ".github/workflows/"],
    "security": ["auth", "security/", "middleware/"],
}


# ---------------------------------------------------------------------------
# Legacy simple outline (kept for backward compat with build_index)
# ---------------------------------------------------------------------------

def _extract_outline(content: str, max_lines: int = 30) -> str:
    """Return a short outline of the file: first N non-empty lines."""
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return "\n".join(lines[:max_lines])


def _is_indexable(path: str, size: int | None) -> bool:
    """Return True if the file is worth indexing."""
    if size is not None and size > _MAX_FILE_SIZE:
        return False
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in _INDEXABLE_EXTENSIONS


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
                embeddings = await self._es.aembed_texts(texts_to_embed)

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

        query_vec: np.ndarray = await self._es.aembed_single(query)

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

    async def query_curated_context(
        self,
        repo_full_name: str,
        branch: str,
        query: str,
        task_type: str | None = None,
        domain: str | None = None,
        max_chars: int | None = None,
    ) -> CuratedCodebaseContext | None:
        """Curated retrieval: semantic search + domain boost + diversity + budget packing."""
        effective_max = max_chars or settings.INDEX_CURATED_MAX_CHARS
        min_sim = settings.INDEX_CURATED_MIN_SIMILARITY
        max_per_dir = settings.INDEX_CURATED_MAX_PER_DIR
        domain_boost = settings.INDEX_DOMAIN_BOOST

        # Fetch all indexed files
        result = await self._db.execute(
            select(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
                RepoFileIndex.embedding.isnot(None),
            )
        )
        rows = result.scalars().all()
        if not rows:
            return None

        # Semantic search
        query_vec = await self._es.aembed_single(query)
        corpus_vecs = [np.frombuffer(r.embedding, dtype=np.float32) for r in rows]
        ranked = self._es.cosine_search(query_vec, corpus_vecs, top_k=len(rows))

        # Relevance filtering + domain boosting
        boosted: list[tuple[int, float]] = []
        domain_patterns = _DOMAIN_PATH_PATTERNS.get(domain or "", [])
        for idx, score in ranked:
            if score < min_sim:
                continue
            effective_score = score
            if domain_patterns:
                path_lower = rows[idx].file_path.lower()
                if any(pat in path_lower for pat in domain_patterns):
                    effective_score *= domain_boost
            boosted.append((idx, effective_score))

        # Re-sort by boosted score
        boosted.sort(key=lambda x: x[1], reverse=True)

        # Diversity selection: max N per directory
        dir_counts: dict[str, int] = {}
        selected: list[tuple[int, float]] = []
        for idx, score in boosted:
            path = rows[idx].file_path
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            if dir_counts.get(directory, 0) >= max_per_dir:
                continue
            dir_counts[directory] = dir_counts.get(directory, 0) + 1
            selected.append((idx, score))

        if not selected:
            return None

        # Budget packing
        parts: list[str] = []
        total_chars = 0
        files_included = 0
        top_score = selected[0][1] if selected else 0.0

        for idx, score in selected:
            row = rows[idx]
            entry = f"## {row.file_path} (relevance: {score:.2f})\n{row.outline or ''}"
            if total_chars + len(entry) > effective_max:
                break
            parts.append(entry)
            total_chars += len(entry)
            files_included += 1

        if not parts:
            return None

        # Freshness: "fresh" (SHA exists), "stale" (meta but no SHA), "unknown" (no meta)
        meta = await self.get_index_status(repo_full_name, branch)
        if meta and meta.head_sha:
            freshness = "fresh"
        elif meta:
            freshness = "stale"
        else:
            freshness = "unknown"

        return CuratedCodebaseContext(
            context_text="\n\n".join(parts),
            files_included=files_included,
            total_files_indexed=len(rows),
            index_freshness=freshness,
            top_relevance_score=top_score,
        )

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
