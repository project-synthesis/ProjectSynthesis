"""Retrieval and synthesis over the pre-built repo index.

Split from ``repo_index_service.py`` (2026-04-19): this module owns
*query-side* concerns — embedding search, domain-aware filtering, source-
type balancing, import-graph expansion, budget packing, and the TTL
cache that fronts curated retrieval. ``RepoIndexService`` retains CRUD +
indexing lifecycle (build / incremental / invalidate) and delegates
query calls here to preserve its public API.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RepoFileIndex, RepoIndexMeta
from app.services.embedding_service import EmbeddingService
from app.services.file_filters import (
    INDEXABLE_EXTENSIONS as _INDEXABLE_EXTENSIONS,
)

logger = logging.getLogger(__name__)


# Module-level TTL cache for curated context results. Scope is
# process-wide. Keys are SHA-256 of ``(repo, branch, prompt, cap)``
# tuples — we can't reverse them to scope by repo, and the TTL is short
# (5 min) so a full-evict after any meaningful index change is cheap
# and correct.
_curated_cache: dict[str, tuple[float, object]] = {}
_CURATED_CACHE_TTL = 300


def invalidate_curated_cache() -> int:
    """Evict all curated cache entries and return the count evicted."""
    count = len(_curated_cache)
    _curated_cache.clear()
    return count


# ---------------------------------------------------------------------------
# CuratedCodebaseContext — result shape for ``query_curated_context``
# ---------------------------------------------------------------------------

@dataclass
class CuratedCodebaseContext:
    context_text: str
    files_included: int
    total_files_indexed: int
    index_freshness: str
    top_relevance_score: float
    selected_files: list[dict] = field(default_factory=list)
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


# ---------------------------------------------------------------------------
# RepoIndexQuery — query-side operations over the pre-built index
# ---------------------------------------------------------------------------

class RepoIndexQuery:
    """Read-side operations over the repo file index.

    Split from ``RepoIndexService`` so indexing lifecycle (build /
    incremental / invalidate) is separated from retrieval + synthesis
    (top-k search, embedding lookup, curated context assembly). The
    indexing service delegates query calls here to preserve its public
    API.
    """

    def __init__(
        self,
        db: AsyncSession,
        embedding_service: EmbeddingService,
    ) -> None:
        self._db = db
        self._es = embedding_service

    # ------------------------------------------------------------------
    # Freshness — needed by curated retrieval
    # ------------------------------------------------------------------

    async def _get_index_status(
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

    # ------------------------------------------------------------------
    # Simple top-k relevance search
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Curated retrieval — semantic search + domain boost + diversity +
    # import-graph expansion + budget packing
    # ------------------------------------------------------------------

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
        meta = await self._get_index_status(repo_full_name, branch)
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


__all__ = [
    "CuratedCodebaseContext",
    "RepoIndexQuery",
    "invalidate_curated_cache",
    # Public-for-tests helpers
    "_classify_source_type",
    "_compute_source_weight",
    "_extract_import_paths",
    "_extract_markdown_references",
    # Module state (referenced by tests)
    "_curated_cache",
]
