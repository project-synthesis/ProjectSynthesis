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
from app.services.github_client import GitHubClient

logger = logging.getLogger(__name__)

# Module-level TTL cache for curated context results
_curated_cache: dict[str, tuple[float, object]] = {}  # key -> (timestamp, result)
_CURATED_CACHE_TTL = 300  # 5 minutes

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
    selected_files: list[dict] = field(default_factory=list)  # [{"path": ..., "score": ...}]
    # Retrieval diagnostics
    stop_reason: str = "unknown"  # "budget" | "relevance_exhausted" | "corpus_empty"
    budget_used_chars: int = 0
    budget_max_chars: int = 0
    diversity_excluded_count: int = 0
    near_misses: list[dict] = field(default_factory=list)  # next 5 files after cutoff


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


# Module-level constants for test file detection (avoid per-call reconstruction)
_TEST_DIRS = frozenset({
    "tests", "test", "__tests__", "spec", "specs",
    "cypress", "playwright", "e2e", "e2e-tests",
    "fixtures", "testdata", "test-data", "test_data",
    "__fixtures__", "__mocks__", "__snapshots__",
})
_TEST_SUFFIXES = (
    "_test", ".test", ".spec", ".stories",
    "_spec", "_bench", "_benchmark",
    ".bench", ".benchmark",
)
_TEST_INFRA = frozenset({
    "conftest.py", "testconfig.py", "test_helpers.py",
    "jest.config.js", "jest.config.ts", "jest.setup.js", "jest.setup.ts",
    "vitest.config.ts", "vitest.config.js", "vitest.setup.ts",
    "playwright.config.ts", "playwright.config.js",
    "cypress.config.ts", "cypress.config.js",
    ".coveragerc", "coverage.config.js",
    "pytest.ini", "setup.cfg", "tox.ini", "noxfile.py",
    "test-setup.ts", "test-setup.js",
})


def _is_test_file(path: str) -> bool:
    """Detect test, spec, benchmark, and test-infrastructure files.

    Tests don't inform prompt optimization context — they duplicate
    information already captured in the source files they test, while
    consuming embedding compute and retrieval budget.
    """
    lower = path.lower()
    segments = lower.split("/")
    basename = segments[-1]

    if any(seg in _TEST_DIRS for seg in segments[:-1]):
        return True
    if basename.startswith("test_") or basename.startswith("tests_"):
        return True
    name_no_ext = basename.rsplit(".", 1)[0] if "." in basename else basename
    if any(name_no_ext.endswith(s) or basename.endswith(s) for s in _TEST_SUFFIXES):
        return True
    if basename in _TEST_INFRA:
        return True
    return False


def _is_indexable(path: str, size: int | None) -> bool:
    """Return True if the file is worth indexing."""
    if size is not None and size > _MAX_FILE_SIZE:
        return False
    dot = path.rfind(".")
    if dot == -1:
        return False
    if path[dot:].lower() not in _INDEXABLE_EXTENSIONS:
        return False
    if _is_test_file(path):
        return False
    return True


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

            code_ext = [item for item in tree if item["path"].rfind(".") != -1 and item["path"][item["path"].rfind("."):].lower() in _INDEXABLE_EXTENSIONS]
            test_excluded = [item for item in code_ext if _is_test_file(item["path"])]
            size_excluded = [item for item in code_ext if item.get("size") is not None and item["size"] > _MAX_FILE_SIZE]
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

            # Phase 1: Read files with bounded concurrency
            t_read = time.monotonic()
            semaphore = asyncio.Semaphore(10)
            contents: list[tuple[dict, str | None]] = await asyncio.gather(
                *[self._read_file(semaphore, token, repo_full_name, branch, item)
                  for item in indexable]
            )
            read_ms = (time.monotonic() - t_read) * 1000
            files_read = sum(1 for _, c in contents if c is not None)
            total_content_chars = sum(len(c) for _, c in contents if c)

            # Phase 2: Extract structured outlines and build rich embedding text
            t_outline = time.monotonic()
            structured = [
                _extract_structured_outline(item["path"], content) if content else None
                for item, content in contents
            ]
            outlines = [
                s.structural_summary if s else ""
                for s in structured
            ]
            texts_to_embed = [
                _build_embedding_text(item["path"], s) if s else item["path"]
                for (item, _), s in zip(contents, structured)
            ]
            outline_ms = (time.monotonic() - t_outline) * 1000

            # Phase 3: Embed
            t_embed = time.monotonic()
            embeddings: list[np.ndarray] = []
            if texts_to_embed:
                embeddings = await self._es.aembed_texts(texts_to_embed)
            embed_ms = (time.monotonic() - t_embed) * 1000

            # Phase 4: Persist file index rows (with full content)
            t_persist = time.monotonic()
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
                    content=content,
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
            persist_ms = (time.monotonic() - t_persist) * 1000
            total_ms = (time.monotonic() - t_start) * 1000

            logger.info(
                "build_index complete: repo=%s files=%d content=%dK "
                "read=%.0fms outline=%.0fms embed=%.0fms persist=%.0fms total=%.0fms",
                repo_full_name, file_count, total_content_chars // 1000,
                read_ms, outline_ms, embed_ms, persist_ms, total_ms,
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
        fetch_ms = (time.monotonic() - t_fetch) * 1000

        # Semantic search
        t_search = time.monotonic()
        query_vec = await self._es.aembed_single(query)
        corpus_vecs = [np.frombuffer(r.embedding, dtype=np.float32) for r in rows]
        ranked = self._es.cosine_search(query_vec, corpus_vecs, top_k=len(rows))
        search_ms = (time.monotonic() - t_search) * 1000

        # Relevance filtering + domain-aware thresholds
        _CROSS_DOMAIN_MIN_SIM = 0.30
        boosted: list[tuple[int, float]] = []
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
            effective_min = _CROSS_DOMAIN_MIN_SIM if is_cross_domain else min_sim
            if score < effective_min:
                if is_cross_domain and score >= min_sim:
                    cross_domain_filtered += 1
                else:
                    below_base_filtered += 1
                continue
            effective_score = score
            if is_same_domain:
                effective_score *= domain_boost
            boosted.append((idx, effective_score))

        # Re-sort by boosted score
        boosted.sort(key=lambda x: x[1], reverse=True)

        # Diversity selection: max N per directory
        dir_counts: dict[str, int] = {}
        selected: list[tuple[int, float]] = []
        diversity_excluded = 0
        for idx, score in boosted:
            path = rows[idx].file_path
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            if dir_counts.get(directory, 0) >= max_per_dir:
                diversity_excluded += 1
                continue
            dir_counts[directory] = dir_counts.get(directory, 0) + 1
            selected.append((idx, score))

        if not selected:
            return None

        filter_ms = (time.monotonic() - t_search) * 1000 - search_ms  # approximate
        logger.info(
            "curated_context: repo=%s query_len=%d indexed=%d "
            "above_threshold=%d cross_domain_cut=%d below_base_cut=%d "
            "diversity_excluded=%d selected=%d top=%.3f "
            "fetch=%.0fms search=%.0fms",
            repo_full_name, len(query), len(rows),
            len(boosted), cross_domain_filtered, below_base_filtered,
            diversity_excluded, len(selected),
            selected[0][1] if selected else 0.0,
            fetch_ms, search_ms,
        )

        # ------------------------------------------------------------------
        # Unified budget packing: similarity + import-graph interleaved
        # ------------------------------------------------------------------
        # Strategy: include the top similarity file first, then its imports,
        # then the next similarity file, then its imports, etc.  This
        # ensures high-value dependency files (models.py, github_client.py)
        # get packed before low-scoring similarity tail files (0.20-0.25)
        # that share vocabulary but aren't functionally related.
        # ------------------------------------------------------------------

        path_to_row_idx = {r.file_path: i for i, r in enumerate(rows)}
        included_paths: set[str] = set()
        parts: list[str] = []
        total_chars = 0
        files_included = 0
        graph_expanded = 0
        top_score = selected[0][1] if selected else 0.0
        selected_files_meta: list[dict] = []
        stop_reason = "relevance_exhausted"

        def _pack_file(row_: RepoFileIndex, score_: float, source_: str) -> bool:
            """Try to add a file to the budget. Returns True if added."""
            nonlocal total_chars, files_included, graph_expanded, stop_reason
            body = row_.content or row_.outline or ""
            if not body:
                return False
            label = f"relevance: {score_:.2f}" if source_ != "import-graph" else "import-graph"
            entry = f"## {row_.file_path} ({label})\n```\n{body}\n```" if row_.content else f"## {row_.file_path} ({label})\n{body}"
            if total_chars + len(entry) > effective_max:
                stop_reason = "budget"
                return False
            parts.append(entry)
            total_chars += len(entry)
            files_included += 1
            included_paths.add(row_.file_path)
            if source_ == "import-graph":
                graph_expanded += 1
            if len(selected_files_meta) < 30:
                selected_files_meta.append({
                    "path": row_.file_path,
                    "score": round(score_, 3),
                    "content_chars": len(body),
                    "source": source_,
                })
            return True

        for idx, score in selected:
            row = rows[idx]
            if row.file_path in included_paths:
                continue
            # Pack this similarity-ranked file
            source = "full" if row.content else "outline"
            if not _pack_file(row, score, source):
                break  # budget exhausted

            # Immediately expand its imports before moving to the next
            # similarity file — this prioritizes direct dependencies over
            # tangentially-related files further down the similarity list.
            if row.content:
                import_paths = _extract_import_paths(row.file_path, row.content)
                for imp in import_paths:
                    if imp in included_paths:
                        continue
                    imp_row_idx = path_to_row_idx.get(imp)
                    if imp_row_idx is None:
                        continue
                    imp_row = rows[imp_row_idx]
                    if not _pack_file(imp_row, 0.0, "import-graph"):
                        break  # budget exhausted — stop expanding this file's imports
                if stop_reason == "budget":
                    break  # budget hit during import expansion

        if graph_expanded:
            logger.info(
                "curated_import_graph: expanded %d files from imports of %d ranked files",
                graph_expanded,
                sum(1 for m in selected_files_meta if m.get("source") != "import-graph"),
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
            "curated_retrieval_detail: files=%d (sim=%d graph=%d) "
            "budget=%d/%d (%.0f%%) stop=%s near_misses=%d total=%.0fms",
            files_included,
            files_included - graph_expanded, graph_expanded,
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
