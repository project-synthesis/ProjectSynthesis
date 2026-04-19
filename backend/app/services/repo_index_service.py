"""Background repo file indexing with SHA-based staleness detection."""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RepoFileIndex, RepoIndexMeta
from app.services.embedding_service import EmbeddingService
from app.services.file_filters import (
    INDEXABLE_EXTENSIONS as _INDEXABLE_EXTENSIONS,
)
from app.services.file_filters import (
    MAX_FILE_SIZE as _MAX_FILE_SIZE,
)
from app.services.file_filters import (
    is_indexable as _is_indexable,
)
from app.services.file_filters import (
    is_test_file as _is_test_file,
)
from app.services.github_client import GitHubApiError, GitHubClient

logger = logging.getLogger(__name__)

# Module-level TTL cache for curated context results
_curated_cache: dict[str, tuple[float, object]] = {}  # key -> (timestamp, result)
_CURATED_CACHE_TTL = 300  # 5 minutes


def invalidate_curated_cache(repo_full_name: str | None = None) -> int:
    """Evict curated cache entries.

    If ``repo_full_name`` is given, only entries whose cache key was built
    from that repo are evicted.  Since cache keys are SHA-256 hashes we
    can't reverse them, so we brute-force evict ALL entries — the TTL is
    short (5 min) and a full-evict after an incremental update is cheap.

    Returns the number of entries evicted.
    """
    count = len(_curated_cache)
    _curated_cache.clear()
    return count

# Extensions, size cap, and test-exclusion primitives are imported from
# ``file_filters`` (re-exported above) so this module + ``codebase_explorer``
# share a single source of truth for what's indexable.


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
class ProcessedFile:
    """A file that has been read, outlined, and embedded — ready for persistence."""
    item: dict  # original tree entry {"path", "sha", "size"}
    content: str
    outline: FileOutline
    embedding: np.ndarray  # 384-dim float32


@dataclass
class CuratedCodebaseContext:
    context_text: str
    files_included: int
    total_files_indexed: int
    index_freshness: str
    top_relevance_score: float
    selected_files: list[dict] = field(default_factory=list)  # [{"path": ..., "score": ...}]
    # Retrieval diagnostics
    stop_reason: str = "unknown"  # "budget" | "relevance_exhausted" | "corpus_empty"
    budget_used_chars: int = 0
    budget_max_chars: int = 0
    diversity_excluded_count: int = 0
    near_misses: list[dict] = field(default_factory=list)  # next 5 files after cutoff
    # Source-type balance diagnostics
    budget_skip_count: int = 0
    doc_files_included: int = 0
    code_files_included: int = 0
    doc_deferred_count: int = 0


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
    # Condensed imports summary
    imports = [
        re.sub(r"\s+", " ", ln.strip())
        for ln in lines
        if re.match(r"^(import |from \S+ import )", ln)
    ]
    imports_summary = "imports: " + ", ".join(
        re.sub(r"^(?:from \S+ )?import ", "", i) for i in imports[:20]
    ) if imports else None
    # __all__ exports
    all_match = re.search(r"^__all__\s*=\s*\[([^\]]+)\]", content, re.MULTILINE)
    if all_match:
        exports = all_match.group(1).replace('"', "").replace("'", "").strip()
        imports_summary = (imports_summary + f" | __all__: [{exports}]") if imports_summary else f"__all__: [{exports}]"
    return FileOutline(
        file_path=path, file_type="python",
        structural_summary="\n".join(sigs),
        imports_summary=imports_summary,
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
    # Svelte 5 runes ($props, $state, $derived)
    runes = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*let\s+.*\$(?:props|state|derived)\(", ln)
    ][:10]
    component_name = path.rsplit("/", 1)[-1].replace(".svelte", "")
    all_sigs = exports + runes
    summary = f"Component: {component_name}\n" + "\n".join(all_sigs)
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


# ---------------------------------------------------------------------------
# Import graph expansion — extract local imports from file content and resolve
# to indexed file paths so curated retrieval can pull in dependencies of
# semantically-selected files.
# ---------------------------------------------------------------------------

# Python: "from app.services.github_client import ..." → "app/services/github_client.py"
_PY_IMPORT_RE = re.compile(
    r"^(?:from|import)\s+(app\.[a-zA-Z0-9_.]+)", re.MULTILINE,
)
# TypeScript/Svelte: "from '$lib/api/client'" → "frontend/src/lib/api/client.ts"
_TS_IMPORT_RE = re.compile(
    r"""from\s+['"](\$lib/[^'"]+|\.\.?/[^'"]+)['"]""", re.MULTILINE,
)


def _extract_import_paths(file_path: str, content: str) -> list[str]:
    """Extract local import paths from file content.

    Returns a list of potential file paths (without extension guessing)
    that can be matched against the RepoFileIndex.
    """
    if not content:
        return []

    candidates: list[str] = []

    if file_path.endswith(".py"):
        for m in _PY_IMPORT_RE.finditer(content):
            # "app.services.github_client" → "backend/app/services/github_client"
            module = m.group(1)
            # Convert dotted module to path — could be a package or module
            rel = module.replace(".", "/")
            candidates.append(f"backend/{rel}.py")
            candidates.append(f"backend/{rel}/__init__.py")

    elif file_path.endswith((".ts", ".tsx", ".js", ".jsx", ".svelte")):
        file_dir = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
        for m in _TS_IMPORT_RE.finditer(content):
            imp = m.group(1)
            if imp.startswith("$lib/"):
                # $lib → frontend/src/lib
                rel = "frontend/src/lib/" + imp[5:]
            elif imp.startswith("."):
                # Relative import — resolve against file's directory
                parts = (file_dir + "/" + imp).split("/")
                resolved: list[str] = []
                for p in parts:
                    if p == "..":
                        if resolved:
                            resolved.pop()
                    elif p != ".":
                        resolved.append(p)
                rel = "/".join(resolved)
            else:
                continue

            # Try common extensions
            for ext in ("", ".ts", ".svelte", ".js", "/index.ts"):
                candidates.append(rel + ext)

    return candidates


_DOMAIN_PATH_PATTERNS: dict[str, list[str]] = {
    "backend": ["backend/", "server/", "api/", "app/"],
    "frontend": ["frontend/", "src/components/", "src/lib/"],
    "database": ["models/", "migrations/", ".sql", "alembic/"],
    "devops": ["docker", "ci/", ".github/workflows/"],
    "security": ["auth", "security/", "middleware/"],
}


# ---------------------------------------------------------------------------
# Source-type classification for curated retrieval balance
# ---------------------------------------------------------------------------

_DOC_EXTENSIONS = frozenset({".md", ".txt", ".rst", ".adoc"})
_DOC_DIRS = ("docs/", "doc/", "plans/", "adr/", "rfcs/", "decisions/")
_CONFIG_EXTENSIONS = frozenset({".yaml", ".yml", ".json", ".toml", ".cfg", ".ini"})


def _classify_source_type(file_path: str) -> str:
    """Classify a file as ``'code'``, ``'docs'``, or ``'config'``.

    Used to enforce source-type diversity in curated retrieval so that
    plan/ADR/doc files don't crowd out implementation code.
    """
    lower = file_path.lower()
    ext = lower[lower.rfind("."):] if "." in lower else ""
    # Docs: markdown-like extensions OR anything under known doc directories
    if ext in _DOC_EXTENSIONS or any(d in lower for d in _DOC_DIRS):
        return "docs"
    # Config: structured data formats (not under docs/)
    if ext in _CONFIG_EXTENSIONS:
        return "config"
    # Code: everything else with an indexable extension
    if ext in _INDEXABLE_EXTENSIONS:
        return "code"
    return "config"


def _compute_source_weight(file_path: str) -> float:
    """Compute multiplicative source-type weight for a file path.

    Applies base weight from ``INDEX_SOURCE_TYPE_WEIGHTS`` config, plus
    additional penalty for ``.github/`` community files.  Root ``README.md``
    is exempt from docs penalty (high-value architectural overview).
    """
    source_type = _classify_source_type(file_path)
    weights = settings.INDEX_SOURCE_TYPE_WEIGHTS
    base_weight = weights.get(source_type, 1.0)

    lower = file_path.lower()

    # Root README is high-value — exempt from docs penalty
    if lower in ("readme.md", "readme.txt", "readme.rst"):
        return 1.0

    # .github/ community files get additional penalty
    if lower.startswith(".github/") and source_type == "docs":
        return base_weight * settings.INDEX_GITHUB_COMMUNITY_PENALTY

    return base_weight


# ---------------------------------------------------------------------------
# Markdown reference extraction for doc-aware import-graph expansion
# ---------------------------------------------------------------------------

# Backtick-wrapped file paths with known directory prefixes
_MD_PATH_RE = re.compile(
    r"`((?:backend|frontend|src|app|lib)/[a-zA-Z0-9_/.-]+\.\w+)`",
)


def _extract_markdown_references(content: str) -> list[str]:
    """Extract backtick-wrapped file-path references from markdown content.

    Conservative: only matches paths starting with known directory prefixes
    (``backend/``, ``frontend/``, ``src/``, ``app/``, ``lib/``).  This avoids
    false positives from inline code examples or prose references.
    """
    if not content:
        return []
    return [m.group(1) for m in _MD_PATH_RE.finditer(content)]


# Bounded scan window for skip-and-continue packing
_MAX_BUDGET_SKIPS = 5


def _classify_github_error(exc: GitHubApiError) -> str:
    """Map a GitHub API error to a human-readable skip reason."""
    if exc.status_code == 401:
        return "token_expired"
    if exc.status_code == 403:
        return "rate_limited"
    if exc.status_code == 404:
        return "repo_not_found"
    return f"github_{exc.status_code}"


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
        t_start = time.monotonic()
        logger.info("build_index started for %s@%s", repo_full_name, branch)
        meta = await self._get_or_create_meta(repo_full_name, branch)
        meta.status = "indexing"
        await self._db.flush()

        try:
            head_sha = await self._gc.get_branch_head_sha(token, repo_full_name, branch)
            tree = await self._gc.get_tree(token, repo_full_name, branch)

            code_ext = [
                item for item in tree
                if item["path"].rfind(".") != -1
                and item["path"][item["path"].rfind("."):].lower() in _INDEXABLE_EXTENSIONS
            ]
            test_excluded = [
                item for item in code_ext if _is_test_file(item["path"])
            ]
            size_excluded = [
                item for item in code_ext
                if item.get("size") is not None and item["size"] > _MAX_FILE_SIZE
            ]
            indexable = [
                item for item in tree
                if _is_indexable(item["path"], item.get("size"))
            ]
            logger.info(
                "build_index filter: repo=%s tree=%d code=%d "
                "tests_excluded=%d size_excluded=%d indexable=%d",
                repo_full_name, len(tree), len(code_ext),
                len(test_excluded), len(size_excluded), len(indexable),
            )

            # Delete existing file index entries for this repo/branch
            await self._db.execute(
                delete(RepoFileIndex).where(
                    RepoFileIndex.repo_full_name == repo_full_name,
                    RepoFileIndex.branch == branch,
                )
            )

            # Phase 1-3: Read, outline, embed via shared pipeline
            t_read = time.monotonic()
            processed, read_failures, embed_failures = await self._read_and_embed_files(
                items=indexable,
                token=token,
                repo_full_name=repo_full_name,
                branch=branch,
                concurrency=10,
            )
            process_ms = (time.monotonic() - t_read) * 1000
            total_content_chars = sum(len(pf.content) for pf in processed)

            if read_failures:
                logger.warning(
                    "build_index: %s@%s %d/%d file reads failed",
                    repo_full_name, branch, read_failures, len(indexable),
                )

            # Phase 4: Persist file index rows
            t_persist = time.monotonic()
            file_count = 0
            for pf in processed:
                row = RepoFileIndex(
                    repo_full_name=repo_full_name,
                    branch=branch,
                    file_path=pf.item["path"],
                    file_sha=pf.item.get("sha"),
                    file_size_bytes=pf.item.get("size"),
                    content=pf.content,
                    outline=pf.outline.structural_summary,
                    embedding=pf.embedding.tobytes(),
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
            persist_ms = (time.monotonic() - t_persist) * 1000
            total_ms = (time.monotonic() - t_start) * 1000

            logger.info(
                "build_index complete: repo=%s files=%d content=%dK "
                "read_failures=%d embed_failures=%d "
                "process=%.0fms persist=%.0fms total=%.0fms",
                repo_full_name, file_count, total_content_chars // 1000,
                read_failures, embed_failures,
                process_ms, persist_ms, total_ms,
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
            np.frombuffer(row.embedding, dtype=np.float32) for row in rows  # type: ignore[arg-type]
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

    async def get_embeddings_by_paths(
        self,
        repo_full_name: str,
        branch: str,
        paths: list[str],
    ) -> dict[str, np.ndarray]:
        """Fetch pre-computed embeddings for specific file paths.

        Returns a dict mapping file_path -> embedding vector for paths
        that exist in the index with non-null embeddings.  Used by
        CodebaseExplorer._rank_files() to reuse richer index embeddings
        (path + docstring + structure) instead of ephemeral path-only ones.
        """
        if not paths:
            return {}

        t0 = time.monotonic()
        result = await self._db.execute(
            select(RepoFileIndex.file_path, RepoFileIndex.embedding).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
                RepoFileIndex.file_path.in_(paths),
                RepoFileIndex.embedding.isnot(None),
            )
        )
        rows = result.all()
        out: dict[str, np.ndarray] = {}
        bad = 0
        for row in rows:
            try:
                vec = np.frombuffer(row.embedding, dtype=np.float32)
                if vec.shape[0] == self._es.dimension:
                    out[row.file_path] = vec
                else:
                    bad += 1
            except Exception:
                bad += 1
        elapsed_ms = (time.monotonic() - t0) * 1000
        if bad:
            logger.warning(
                "get_embeddings_by_paths: %d/%d embeddings had bad shape/data "
                "for %s@%s (%.0fms)",
                bad, len(rows), repo_full_name, branch, elapsed_ms,
            )
        logger.debug(
            "get_embeddings_by_paths: returned %d/%d requested for %s@%s in %.0fms",
            len(out), len(paths), repo_full_name, branch, elapsed_ms,
        )
        return out

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
        # TTL cache check
        cache_key = hashlib.sha256(
            f"{repo_full_name}:{branch}:{query[:200]}:{task_type}:{domain}".encode()
        ).hexdigest()[:16]
        cached = _curated_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CURATED_CACHE_TTL:
            return cached[1]  # type: ignore[return-value]
        effective_max = max_chars or settings.INDEX_CURATED_MAX_CHARS
        min_sim = settings.INDEX_CURATED_MIN_SIMILARITY
        max_per_dir = settings.INDEX_CURATED_MAX_PER_DIR
        domain_boost = settings.INDEX_DOMAIN_BOOST

        t_curated_start = time.monotonic()

        # Fetch all indexed files
        t_fetch = time.monotonic()
        fetch_result = await self._db.execute(
            select(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
                RepoFileIndex.embedding.isnot(None),
            )
        )
        rows = fetch_result.scalars().all()
        if not rows:
            return None
        fetch_ms = (time.monotonic() - t_fetch) * 1000

        # Semantic search
        t_search = time.monotonic()
        query_vec = await self._es.aembed_single(query)
        corpus_vecs = [np.frombuffer(r.embedding, dtype=np.float32) for r in rows]  # type: ignore[arg-type]
        ranked = self._es.cosine_search(query_vec, corpus_vecs, top_k=len(rows))
        search_ms = (time.monotonic() - t_search) * 1000

        # Relevance filtering + domain-aware thresholds
        cross_domain_min_sim = 0.30
        boosted: list[tuple[int, float]] = []
        raw_score_by_path: dict[str, float] = {}  # D1: track pre-weighted scores for metadata
        domain_patterns = _DOMAIN_PATH_PATTERNS.get(domain or "", [])
        cross_domain_filtered = 0
        below_base_filtered = 0
        for idx, score in ranked:
            path_lower = rows[idx].file_path.lower()

            # Check if this file belongs to ANY known domain
            file_domain: str | None = None
            for d, pats in _DOMAIN_PATH_PATTERNS.items():
                if any(pat in path_lower for pat in pats):
                    file_domain = d
                    break

            is_same_domain = bool(domain_patterns) and any(
                pat in path_lower for pat in domain_patterns
            )
            # Apply stricter threshold when the file belongs to a known domain
            # that differs from the prompt's domain (cross-domain noise filter)
            is_cross_domain = file_domain is not None and not is_same_domain
            effective_min = cross_domain_min_sim if is_cross_domain else min_sim
            if score < effective_min:
                if is_cross_domain and score >= min_sim:
                    cross_domain_filtered += 1
                else:
                    below_base_filtered += 1
                continue
            raw_score_by_path[rows[idx].file_path] = score
            # D1: Source-type weighting — code > config > docs
            source_weight = _compute_source_weight(rows[idx].file_path)
            effective_score = score * source_weight
            # Domain boost stacks multiplicatively
            if is_same_domain:
                effective_score *= domain_boost
            boosted.append((idx, effective_score))

        # Re-sort by boosted score
        boosted.sort(key=lambda x: x[1], reverse=True)

        # Diversity selection: max N per directory + source-type balance
        doc_cap_ratio = settings.INDEX_CURATED_DOC_CAP_RATIO
        dir_counts: dict[str, int] = {}
        selected: list[tuple[int, float]] = []
        deferred_docs: list[tuple[int, float]] = []
        diversity_excluded = 0
        doc_selected = 0
        for idx, score in boosted:
            path = rows[idx].file_path
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            if dir_counts.get(directory, 0) >= max_per_dir:
                diversity_excluded += 1
                continue
            dir_counts[directory] = dir_counts.get(directory, 0) + 1

            # Source-type soft cap: defer excess docs so code files get slots
            if _classify_source_type(path) == "docs":
                total_so_far = len(selected) + 1
                if (
                    len(selected) >= 2
                    and (doc_selected + 1) > total_so_far * doc_cap_ratio
                ):
                    deferred_docs.append((idx, score))
                    continue
                doc_selected += 1
            selected.append((idx, score))

        # Re-insert deferred docs at the end — available for packing if
        # budget allows after code files, just deprioritized.
        selected.extend(deferred_docs)

        if not selected:
            return None

        logger.info(
            "curated_context: repo=%s query_len=%d indexed=%d "
            "above_threshold=%d cross_domain_cut=%d below_base_cut=%d "
            "diversity_excluded=%d doc_deferred=%d selected=%d top=%.3f "
            "fetch=%.0fms search=%.0fms",
            repo_full_name, len(query), len(rows),
            len(boosted), cross_domain_filtered, below_base_filtered,
            diversity_excluded, len(deferred_docs), len(selected),
            selected[0][1] if selected else 0.0,
            fetch_ms, search_ms,
        )

        # ------------------------------------------------------------------
        # Unified budget packing: similarity + dependency expansion
        # ------------------------------------------------------------------
        # Strategy: include the top similarity file first, then its
        # dependencies (code imports or markdown file-path references),
        # then the next similarity file, then its dependencies, etc.
        #
        # Skip-and-continue: when a file doesn't fit the remaining budget,
        # skip it and try the next one (up to _MAX_BUDGET_SKIPS consecutive
        # skips).  This prevents a single oversized file from wasting half
        # the budget when smaller high-value files would still fit.
        # ------------------------------------------------------------------

        path_to_row_idx = {r.file_path: i for i, r in enumerate(rows)}
        included_paths: set[str] = set()
        parts: list[str] = []
        total_chars = 0
        files_included = 0
        graph_from_imports = 0
        graph_from_doc_refs = 0
        doc_files_packed = 0
        code_files_packed = 0
        budget_skip_count = 0
        top_score = selected[0][1] if selected else 0.0
        selected_files_meta: list[dict] = []
        stop_reason = "relevance_exhausted"

        def _pack_file(row_: RepoFileIndex, score_: float, source_: str) -> bool:
            """Try to add a file to the budget.  Returns True if added.

            Pure budget check — does NOT set ``stop_reason``.  The caller
            decides the final stop reason based on how the loop terminates.
            """
            nonlocal total_chars, files_included
            nonlocal graph_from_imports, graph_from_doc_refs
            nonlocal doc_files_packed, code_files_packed
            body = row_.content or row_.outline or ""
            if not body:
                return False
            label = (
                f"relevance: {score_:.2f}"
                if source_ not in ("import-graph", "doc-ref")
                else source_
            )
            header = f"## {row_.file_path} ({label})"
            entry = f"{header}\n```\n{body}\n```" if row_.content else f"{header}\n{body}"
            if total_chars + len(entry) > effective_max:
                return False
            parts.append(entry)
            total_chars += len(entry)
            files_included += 1
            included_paths.add(row_.file_path)
            if source_ == "import-graph":
                graph_from_imports += 1
            elif source_ == "doc-ref":
                graph_from_doc_refs += 1
            st = _classify_source_type(row_.file_path)
            if st == "docs":
                doc_files_packed += 1
            elif st == "code":
                code_files_packed += 1
            if len(selected_files_meta) < 30:
                selected_files_meta.append({
                    "path": row_.file_path,
                    "score": round(score_, 3),
                    "raw_similarity": round(raw_score_by_path.get(row_.file_path, score_), 3),
                    "source_weight": round(_compute_source_weight(row_.file_path), 2),
                    "content_chars": len(body),
                    "source": source_,
                    "source_type": st,
                })
            return True

        budget_skips = 0
        for idx, score in selected:
            row = rows[idx]
            if row.file_path in included_paths:
                continue
            # Pack this similarity-ranked file
            source = "full" if row.content else "outline"
            if not _pack_file(row, score, source):
                budget_skips += 1
                budget_skip_count += 1
                if budget_skips >= _MAX_BUDGET_SKIPS:
                    stop_reason = "budget"
                    break
                continue  # skip oversized file, try next
            budget_skips = 0  # reset on successful pack

            # Expand dependencies: code imports for .py/.ts, backtick
            # file-path references for docs.  Dependencies are packed
            # before moving to the next similarity-ranked file so that
            # high-value transitive files (models.py, github_client.py)
            # take priority over low-scoring similarity tail files.
            if row.content:
                is_doc = _classify_source_type(row.file_path) == "docs"
                if is_doc:
                    ref_paths = _extract_markdown_references(row.content)
                else:
                    ref_paths = _extract_import_paths(row.file_path, row.content)
                ref_source = "doc-ref" if is_doc else "import-graph"
                for ref in ref_paths:
                    if ref in included_paths:
                        continue
                    ref_row_idx = path_to_row_idx.get(ref)
                    if ref_row_idx is None:
                        continue
                    ref_row = rows[ref_row_idx]
                    _pack_file(ref_row, 0.0, ref_source)
                    # Don't break on ref failure — skip and continue

        graph_expanded = graph_from_imports + graph_from_doc_refs
        if graph_expanded:
            logger.info(
                "curated_import_graph: expanded %d files "
                "(from_imports=%d from_doc_refs=%d) "
                "of %d ranked files",
                graph_expanded,
                graph_from_imports, graph_from_doc_refs,
                sum(
                    1 for m in selected_files_meta
                    if m.get("source") not in ("import-graph", "doc-ref")
                ),
            )

        # Near misses: next 5 similarity-ranked files that weren't included
        near_misses: list[dict] = []
        for idx, score in selected:
            if len(near_misses) >= 5:
                break
            if rows[idx].file_path not in included_paths:
                near_misses.append({
                    "path": rows[idx].file_path,
                    "score": round(score, 3),
                    "raw_similarity": round(raw_score_by_path.get(rows[idx].file_path, score), 3),
                    "source_weight": round(_compute_source_weight(rows[idx].file_path), 2),
                })

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

        curated_total_ms = (time.monotonic() - t_curated_start) * 1000
        logger.info(
            "curated_retrieval_detail: files=%d (code=%d docs=%d) "
            "graph=%d (imports=%d doc_refs=%d) skips=%d "
            "budget=%d/%d (%.0f%%) stop=%s near_misses=%d total=%.0fms",
            files_included, code_files_packed, doc_files_packed,
            graph_expanded, graph_from_imports, graph_from_doc_refs,
            budget_skip_count,
            total_chars, effective_max,
            total_chars / max(effective_max, 1) * 100,
            stop_reason, len(near_misses), curated_total_ms,
        )

        result = CuratedCodebaseContext(
            context_text="\n\n".join(parts),
            files_included=files_included,
            total_files_indexed=len(rows),
            index_freshness=freshness,
            top_relevance_score=top_score,
            selected_files=selected_files_meta,
            stop_reason=stop_reason,
            budget_used_chars=total_chars,
            budget_max_chars=effective_max,
            diversity_excluded_count=diversity_excluded,
            near_misses=near_misses,
            budget_skip_count=budget_skip_count,
            doc_files_included=doc_files_packed,
            code_files_included=code_files_packed,
            doc_deferred_count=len(deferred_docs),
        )
        # Cache result
        _curated_cache[cache_key] = (time.time(), result)
        # Evict stale entries (simple size cap)
        if len(_curated_cache) > 100:
            oldest = min(_curated_cache, key=lambda k: _curated_cache[k][0])
            del _curated_cache[oldest]
        return result

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
    # Incremental refresh — detect changed files and re-embed only those
    # ------------------------------------------------------------------

    async def incremental_update(
        self,
        repo_full_name: str,
        branch: str,
        token: str,
        concurrency: int = 5,
    ) -> dict:
        """Incrementally update the index by diffing the current HEAD tree
        against stored ``file_sha`` values.

        Only touches rows for files that actually changed, were added, or
        were removed.  Never calls ``build_index()`` or bulk-deletes index
        entries.  Skips if a concurrent ``build_index()`` is in progress
        (``status == 'indexing'``).

        Returns a summary dict::

            {
                "changed": int, "added": int, "removed": int,
                "read_failures": int, "embed_failures": int,
                "skipped_reason": str | None,
                "elapsed_ms": float,
            }
        """
        t_start = time.monotonic()
        repo_tag = f"{repo_full_name}@{branch}"

        def _result(
            changed: int = 0, added: int = 0, removed: int = 0,
            read_failures: int = 0, embed_failures: int = 0,
            skipped_reason: str | None = None,
        ) -> dict:
            return {
                "changed": changed, "added": added, "removed": removed,
                "read_failures": read_failures, "embed_failures": embed_failures,
                "skipped_reason": skipped_reason,
                "elapsed_ms": round((time.monotonic() - t_start) * 1000, 1),
            }

        # ── Guard: meta must exist (build_index must have run) ───────���
        meta = await self.get_index_status(repo_full_name, branch)
        if meta is None:
            logger.info("incremental_update: %s no meta — skipping (needs build_index)", repo_tag)
            return _result(skipped_reason="no_index")

        if meta.status == "indexing":
            logger.info("incremental_update: %s currently indexing — skipping", repo_tag)
            return _result(skipped_reason="indexing")

        # ── Step 1: Quick HEAD SHA check (1 API call) ─────────────────
        t_sha = time.monotonic()
        try:
            current_sha = await self._gc.get_branch_head_sha(token, repo_full_name, branch)
        except GitHubApiError as exc:
            reason = _classify_github_error(exc)
            logger.warning("incremental_update: %s HEAD check failed: %s (%s)", repo_tag, exc, reason)
            return _result(skipped_reason=reason)
        except Exception as exc:
            logger.warning("incremental_update: %s HEAD check failed: %s", repo_tag, exc)
            return _result(skipped_reason="network_error")
        sha_ms = (time.monotonic() - t_sha) * 1000

        if current_sha and meta.head_sha == current_sha:
            logger.debug(
                "incremental_update: %s HEAD unchanged (%s) sha_check=%.0fms",
                repo_tag, current_sha[:8], sha_ms,
            )
            return _result(skipped_reason="head_unchanged")

        # ── Step 2: Fetch full tree (1 API call) ──────────────────────
        t_tree = time.monotonic()
        try:
            tree = await self._gc.get_tree(token, repo_full_name, branch)
        except GitHubApiError as exc:
            reason = _classify_github_error(exc)
            logger.warning("incremental_update: %s tree fetch failed: %s (%s)", repo_tag, exc, reason)
            return _result(skipped_reason=reason)
        except Exception as exc:
            logger.warning("incremental_update: %s tree fetch failed: %s", repo_tag, exc)
            return _result(skipped_reason="network_error")
        tree_ms = (time.monotonic() - t_tree) * 1000

        # Build lookup from current tree (only indexable files)
        tree_map: dict[str, dict] = {}
        for item in tree:
            if _is_indexable(item["path"], item.get("size")):
                tree_map[item["path"]] = item

        # Load indexed file paths and SHAs
        db_result = await self._db.execute(
            select(
                RepoFileIndex.file_path,
                RepoFileIndex.file_sha,
            ).where(
                RepoFileIndex.repo_full_name == repo_full_name,
                RepoFileIndex.branch == branch,
            )
        )
        indexed_map: dict[str, str | None] = {
            row.file_path: row.file_sha for row in db_result.all()
        }

        # ── Step 3: Classify into changed / added / removed ──────────
        t_diff = time.monotonic()
        changed_items: list[dict] = []
        added_items: list[dict] = []
        removed_paths: list[str] = []

        for path, item in tree_map.items():
            if path not in indexed_map:
                added_items.append(item)
            elif item.get("sha") and indexed_map[path] != item["sha"]:
                changed_items.append(item)

        for path in indexed_map:
            if path not in tree_map:
                removed_paths.append(path)
        diff_ms = (time.monotonic() - t_diff) * 1000

        total_delta = len(changed_items) + len(added_items) + len(removed_paths)
        if total_delta == 0:
            # SHA differs but no file-level changes (e.g. merge commit)
            meta.head_sha = current_sha
            await self._db.commit()
            logger.info(
                "incremental_update: %s HEAD changed (%s→%s) but no file diffs "
                "sha=%.0fms tree=%.0fms",
                repo_tag, (meta.head_sha or "?")[:8], (current_sha or "?")[:8],
                sha_ms, tree_ms,
            )
            return _result()

        logger.info(
            "incremental_diff: repo=%s changed=%d added=%d removed=%d "
            "tree=%d indexed=%d sha=%.0fms tree=%.0fms diff=%.0fms",
            repo_tag,
            len(changed_items), len(added_items), len(removed_paths),
            len(tree_map), len(indexed_map),
            sha_ms, tree_ms, diff_ms,
        )

        # ── Step 4: Delete removed file rows ──────────────────────────
        t_delete = time.monotonic()
        if removed_paths:
            await self._db.execute(
                delete(RepoFileIndex).where(
                    RepoFileIndex.repo_full_name == repo_full_name,
                    RepoFileIndex.branch == branch,
                    RepoFileIndex.file_path.in_(removed_paths),
                )
            )
        delete_ms = (time.monotonic() - t_delete) * 1000

        # ── Step 5-6: Read, embed, and upsert changed/added files ─────
        t_process = time.monotonic()
        to_process = changed_items + added_items
        processed, read_failures, embed_failures = await self._read_and_embed_files(
            items=to_process,
            token=token,
            repo_full_name=repo_full_name,
            branch=branch,
            concurrency=concurrency,
        )
        process_ms = (time.monotonic() - t_process) * 1000

        if read_failures:
            logger.warning(
                "incremental_update: %s %d/%d file reads failed",
                repo_tag, read_failures, len(to_process),
            )

        t_persist = time.monotonic()
        upserted = 0
        for pf in processed:
            try:
                stmt = sqlite_insert(RepoFileIndex).values(
                    id=str(uuid.uuid4()),
                    repo_full_name=repo_full_name,
                    branch=branch,
                    file_path=pf.item["path"],
                    file_sha=pf.item.get("sha"),
                    file_size_bytes=pf.item.get("size"),
                    content=pf.content,
                    outline=pf.outline.structural_summary,
                    embedding=pf.embedding.tobytes(),
                    updated_at=datetime.now(timezone.utc),
                ).on_conflict_do_update(
                    index_elements=["repo_full_name", "branch", "file_path"],
                    set_={
                        "file_sha": pf.item.get("sha"),
                        "file_size_bytes": pf.item.get("size"),
                        "content": pf.content,
                        "outline": pf.outline.structural_summary,
                        "embedding": pf.embedding.tobytes(),
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                await self._db.execute(stmt)
                upserted += 1
            except Exception as db_exc:
                logger.warning(
                    "incremental_update: %s upsert failed for %s: %s",
                    repo_tag, pf.item["path"], db_exc,
                )
        persist_ms = (time.monotonic() - t_persist) * 1000

        # ── Step 7: Update meta ───────────────────────────────────────
        meta.head_sha = current_sha
        new_count = (meta.file_count or 0) + len(added_items) - len(removed_paths)
        meta.file_count = max(0, new_count)  # guard against negative
        meta.indexed_at = datetime.now(timezone.utc)
        await self._db.commit()

        total_ms = (time.monotonic() - t_start) * 1000
        logger.info(
            "incremental_update_complete: repo=%s changed=%d added=%d removed=%d "
            "upserted=%d read_fail=%d embed_fail=%d "
            "sha=%.0fms tree=%.0fms diff=%.0fms delete=%.0fms "
            "process=%.0fms persist=%.0fms total=%.0fms",
            repo_tag,
            len(changed_items), len(added_items), len(removed_paths),
            upserted, read_failures, embed_failures,
            sha_ms, tree_ms, diff_ms, delete_ms,
            process_ms, persist_ms, total_ms,
        )

        return _result(
            changed=len(changed_items),
            added=len(added_items),
            removed=len(removed_paths),
            read_failures=read_failures,
            embed_failures=embed_failures,
        )

    # ------------------------------------------------------------------
    # Shared file processing pipeline
    # ------------------------------------------------------------------

    async def _read_and_embed_files(
        self,
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
            *[self._read_file(semaphore, token, repo_full_name, branch, it)
              for it in items]
        )

        # Phase B: Filter out failed reads, extract outlines + embedding text
        read_failures = 0
        valid: list[tuple[dict, str, FileOutline, str]] = []  # (item, content, outline, embed_text)
        for item, content in raw:
            if content is None:
                read_failures += 1
                continue
            outline = _extract_structured_outline(item["path"], content)
            embed_text = _build_embedding_text(item["path"], outline)
            valid.append((item, content, outline, embed_text))

        if not valid:
            return [], read_failures, 0

        # Phase C: Batch embed (with fallback to zero vectors on failure)
        embed_failures = 0
        try:
            embeddings = await self._es.aembed_texts(
                [embed_text for _, _, _, embed_text in valid]
            )
        except Exception as exc:
            logger.error(
                "_read_and_embed_files: embedding failed for %s@%s (%s) — "
                "persisting %d files with zero vectors",
                repo_full_name, branch, exc, len(valid),
            )
            embeddings = []
            embed_failures = len(valid)

        # Phase D: Assemble ProcessedFile results
        zero_vec = np.zeros(384, dtype=np.float32)
        processed: list[ProcessedFile] = []
        for idx, (item, content, outline, _) in enumerate(valid):
            vec = embeddings[idx] if idx < len(embeddings) else zero_vec
            processed.append(ProcessedFile(
                item=item,
                content=content,
                outline=outline,
                embedding=vec.astype(np.float32),
            ))

        return processed, read_failures, embed_failures

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_or_create_meta(
        self, repo_full_name: str, branch: str
    ) -> RepoIndexMeta:
        # Atomic upsert — prevents duplicate rows from concurrent calls
        stmt = sqlite_insert(RepoIndexMeta).values(
            id=str(uuid.uuid4()),
            repo_full_name=repo_full_name,
            branch=branch,
            status="pending",
            file_count=0,
        ).on_conflict_do_nothing(
            index_elements=["repo_full_name", "branch"],
        )
        await self._db.execute(stmt)
        await self._db.flush()

        # Fetch the (possibly pre-existing) row
        meta = await self.get_index_status(repo_full_name, branch)
        assert meta is not None, (
            f"RepoIndexMeta for {repo_full_name}@{branch} must exist after upsert"
        )
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
