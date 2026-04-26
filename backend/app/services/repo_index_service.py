"""Background repo file indexing with SHA-based staleness detection.

Lifecycle owner — this module handles build / incremental / invalidate
(CRUD + staleness) plus the structured-outline extractors that feed
embedding text. Query-side concerns (relevance search, curated context,
import-graph expansion, TTL caches for retrieval) live in
``repo_index_query.py``; ``RepoIndexService`` delegates its query
methods there so the public API stays unchanged.
"""

import logging
import time
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

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

# File-reader + outline modules are re-exported at the bottom for backward compat.
from app.services.repo_index_file_reader import (
    ProcessedFile,
    invalidate_file_cache,
    read_and_embed_files,
)
from app.services.repo_index_outlines import (
    FileOutline,
)
from app.services.repo_index_outlines import (
    build_content_sha as _build_content_sha,
)
from app.services.repo_index_outlines import (
    extract_structured_outline as _extract_structured_outline,
)

# Query-side symbols are re-exported at the bottom for backward compat with
# ``from app.services.repo_index_service import ...`` call sites.
from app.services.repo_index_query import (
    CuratedCodebaseContext,
    RepoIndexQuery,
    _classify_source_type,
    _compute_source_weight,
    _curated_cache,
    _extract_import_paths,
    _extract_markdown_references,
    invalidate_curated_cache,
)

logger = logging.getLogger(__name__)


async def _publish_phase_change(
    repo_full_name: str,
    branch: str,
    *,
    phase: str,
    status: str,
    files_seen: int = 0,
    files_total: int = 0,
    error: str | None = None,
) -> None:
    """Publish `index_phase_changed` SSE event — never raises.

    C2 SSE wiring: frontend subscribes to know when to flip from "indexing"
    to "ready", surface errors, and show progress. Import is local to avoid
    a circular import via app.services.event_bus at module-load time.
    """
    try:
        from app.services.event_bus import event_bus
        payload = {
            "repo_full_name": repo_full_name,
            "branch": branch,
            "phase": phase,
            "status": status,
            "files_seen": files_seen,
            "files_total": files_total,
        }
        if error is not None:
            payload["error"] = error
        event_bus.publish("index_phase_changed", payload)
    except Exception:
        logger.debug("index_phase_changed publish failed", exc_info=True)


# Extensions, size cap, and test-exclusion primitives are imported from
# ``file_filters`` (re-exported above) so this module + ``codebase_explorer``
# share a single source of truth for what's indexable.
#
# ``FileOutline`` + ``ProcessedFile`` + ``read_and_embed_files`` +
# ``invalidate_file_cache`` are re-exported from their new homes at the
# bottom of this module.


def _classify_github_error(exc: GitHubApiError) -> str:
    """Map a GitHub API error to a human-readable skip reason."""
    if exc.status_code == 401:
        return "token_expired"
    if exc.status_code == 403:
        return "rate_limited"
    if exc.status_code == 404:
        return "repo_not_found"
    return f"github_{exc.status_code}"


# Per-process noise suppression for incremental-update auth failures.
# Without this, every refresh cycle (default every 600s) logs WARNING
# "HEAD check failed: GitHub API 401: Bad credentials (token_expired)".
# The condition is real but the operator sees it once and there's no
# action they can take from a log message — token refresh is automatic
# on the next user-side API call.  After the first WARNING per
# (repo_tag, reason), subsequent repeats demote to DEBUG; the
# (repo_tag, reason) pair clears on a successful update.
_SUPPRESSED_REFRESH_REASONS: frozenset[str] = frozenset(
    {"token_expired", "rate_limited", "auth_invalid"},
)
_seen_refresh_warning: set[tuple[str, str]] = set()


def _log_refresh_warning(repo_tag: str, reason: str, message: str) -> None:
    """Log a refresh-time warning, suppressing repeat (repo, reason) pairs.

    First occurrence logs at WARNING; subsequent repeats go to DEBUG.
    Reset by ``mark_refresh_recovered`` when the refresh succeeds again.
    """
    key = (repo_tag, reason)
    if reason in _SUPPRESSED_REFRESH_REASONS and key in _seen_refresh_warning:
        logger.debug("incremental_update (suppressed-repeat): %s", message)
        return
    _seen_refresh_warning.add(key)
    logger.warning("incremental_update: %s", message)


def _mark_refresh_recovered(repo_tag: str) -> None:
    """Clear suppression for a repo once it refreshes cleanly again."""
    for reason in tuple(_SUPPRESSED_REFRESH_REASONS):
        _seen_refresh_warning.discard((repo_tag, reason))


# ---------------------------------------------------------------------------
# RepoIndexService — indexing lifecycle (build / incremental / invalidate)
# ---------------------------------------------------------------------------

class RepoIndexService:
    """Manages background indexing of GitHub repo files for semantic search.

    Owns the lifecycle half of the repo-index contract: full builds,
    incremental SHA-diff refreshes, and invalidation. Query-side
    operations (relevance search, curated retrieval, path→embedding
    lookup) are delegated to ``RepoIndexQuery`` so this class stays
    focused on mutation paths. The delegator methods preserve the
    original public API — existing callers continue to work unchanged.
    """

    def __init__(
        self,
        db: AsyncSession,
        github_client: GitHubClient,
        embedding_service: EmbeddingService,
    ) -> None:
        self._db = db
        self._gc = github_client
        self._es = embedding_service
        # Query-side delegate. Sharing the same ``db`` + ``embedding_service``
        # keeps the single-session invariant intact; keeping it private
        # lets the callsites treat ``RepoIndexService`` as a single
        # cohesive API while the implementation splits cleanly in two.
        self._query = RepoIndexQuery(db=db, embedding_service=embedding_service)

    # ------------------------------------------------------------------
    # Public query API — delegates to RepoIndexQuery
    # ------------------------------------------------------------------

    async def query_relevant_files(
        self,
        repo_full_name: str,
        branch: str,
        query: str,
        top_k: int = 10,
    ) -> list[dict]:
        """Embed query and cosine-search the pre-built index."""
        return await self._query.query_relevant_files(
            repo_full_name, branch, query, top_k=top_k,
        )

    async def get_embeddings_by_paths(
        self,
        repo_full_name: str,
        branch: str,
        paths: list[str],
    ) -> dict[str, np.ndarray]:
        """Fetch pre-computed embeddings for specific file paths."""
        return await self._query.get_embeddings_by_paths(
            repo_full_name, branch, paths,
        )

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
        return await self._query.query_curated_context(
            repo_full_name, branch, query,
            task_type=task_type, domain=domain, max_chars=max_chars,
        )

    # ------------------------------------------------------------------
    # Indexing lifecycle
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
        meta.index_phase = "fetching_tree"
        meta.files_seen = 0
        meta.files_total = 0
        meta.error_message = None
        await self._db.commit()
        await _publish_phase_change(
            repo_full_name, branch,
            phase="fetching_tree", status="indexing",
            files_seen=0, files_total=0,
        )

        try:
            head_sha = await self._gc.get_branch_head_sha(token, repo_full_name, branch)

            # ETag-conditioned tree fetch. If the stored etag is still valid
            # we get a 304 and can short-circuit the whole rebuild — GitHub
            # counts 304 responses as "no content served" for the primary
            # rate limit, so this is the cheap path. We only send the etag
            # when we actually have cached rows to trust; a stale etag
            # without rows would leave us with nothing to serve from.
            etag_to_send: str | None = None
            if meta.tree_etag and (meta.file_count or 0) > 0:
                etag_to_send = meta.tree_etag
            tree, new_tree_etag = await self._gc.get_tree_with_cache(
                token, repo_full_name, branch, etag=etag_to_send,
            )

            if tree is None:
                # 304 Not Modified. Reuse existing file rows; just refresh
                # head_sha (which may have advanced via a tag or merge
                # commit that didn't touch any tree blobs) and mark ready.
                logger.info(
                    "build_index: %s@%s tree unchanged (304) — "
                    "reusing %d existing rows",
                    repo_full_name, branch, meta.file_count or 0,
                )
                meta.status = "ready"
                meta.index_phase = "embedding"
                meta.head_sha = head_sha
                meta.files_seen = meta.file_count or 0
                meta.files_total = meta.file_count or 0
                meta.error_message = None
                meta.indexed_at = datetime.now(timezone.utc)
                # tree_etag intentionally unchanged (304 echoed it).
                await self._db.commit()
                await _publish_phase_change(
                    repo_full_name, branch,
                    phase="embedding", status="ready",
                    files_seen=meta.file_count or 0,
                    files_total=meta.file_count or 0,
                )
                return

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

            # Transition to embedding phase + record total file count
            meta.index_phase = "embedding"
            meta.files_total = len(indexable)
            await self._db.commit()
            await _publish_phase_change(
                repo_full_name, branch,
                phase="embedding", status="indexing",
                files_seen=0, files_total=len(indexable),
            )

            # Phase 1-3: Read, outline, embed via shared pipeline
            t_read = time.monotonic()
            processed, read_failures, embed_failures = await read_and_embed_files(
                db=self._db,
                embedding_service=self._es,
                github_client=self._gc,
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
                    content_sha=pf.content_sha,
                    embedding=pf.embedding.tobytes(),
                )
                self._db.add(row)
                file_count += 1

            # Update meta — file indexing done; synthesis owns the flip to "ready".
            meta.status = "ready"
            meta.head_sha = head_sha
            meta.file_count = file_count
            meta.files_seen = file_count
            meta.files_total = len(indexable)
            meta.error_message = None
            meta.indexed_at = datetime.now(timezone.utc)
            # Persist the fresh ETag so the next build_index can try 304.
            if new_tree_etag:
                meta.tree_etag = new_tree_etag
            await self._db.commit()
            # Cache safety: a full rebuild replaced every RepoFileIndex row, so
            # any cached curated retrieval keyed on (repo, branch, query, …) is
            # now stale. Flush the TTL cache unconditionally. TTL is 5 minutes
            # so warm-up cost is trivial; correctness over caching.
            invalidate_curated_cache()
            await _publish_phase_change(
                repo_full_name, branch,
                phase="embedding", status="ready",
                files_seen=file_count, files_total=len(indexable),
            )
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
            meta.index_phase = "error"
            meta.error_message = str(exc)
            await self._db.commit()
            await _publish_phase_change(
                repo_full_name, branch,
                phase="error", status="error",
                files_seen=meta.files_seen, files_total=meta.files_total,
                error=str(exc),
            )
            raise

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

        # ── Guard: meta must exist (build_index must have run) ───────────
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
            _log_refresh_warning(
                repo_tag, reason,
                f"{repo_tag} HEAD check failed: {exc} ({reason})",
            )
            return _result(skipped_reason=reason)
        except Exception as exc:
            _log_refresh_warning(
                repo_tag, "network_error",
                f"{repo_tag} HEAD check failed: {exc}",
            )
            return _result(skipped_reason="network_error")
        else:
            _mark_refresh_recovered(repo_tag)
        sha_ms = (time.monotonic() - t_sha) * 1000

        if current_sha and meta.head_sha == current_sha:
            logger.debug(
                "incremental_update: %s HEAD unchanged (%s) sha_check=%.0fms",
                repo_tag, current_sha[:8], sha_ms,
            )
            return _result(skipped_reason="head_unchanged")

        # ── Step 2: Fetch full tree with ETag (1 API call, free on 304) ─
        t_tree = time.monotonic()
        try:
            tree, new_tree_etag = await self._gc.get_tree_with_cache(
                token, repo_full_name, branch, etag=meta.tree_etag,
            )
        except GitHubApiError as exc:
            reason = _classify_github_error(exc)
            logger.warning("incremental_update: %s tree fetch failed: %s (%s)", repo_tag, exc, reason)
            return _result(skipped_reason=reason)
        except Exception as exc:
            logger.warning("incremental_update: %s tree fetch failed: %s", repo_tag, exc)
            return _result(skipped_reason="network_error")
        tree_ms = (time.monotonic() - t_tree) * 1000

        # 304: tree unchanged (HEAD may have moved via a tag / empty
        # merge). Skip file-level diffing — just advance head_sha so the
        # step-1 short-circuit catches the next poll.
        if tree is None:
            meta.head_sha = current_sha
            await self._db.commit()
            logger.info(
                "incremental_update: %s tree unchanged (304) head=%s→%s "
                "sha=%.0fms tree=%.0fms",
                repo_tag, (meta.head_sha or "?")[:8], (current_sha or "?")[:8],
                sha_ms, tree_ms,
            )
            return _result(skipped_reason="tree_unchanged")

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
        processed, read_failures, embed_failures = await read_and_embed_files(
            db=self._db,
            embedding_service=self._es,
            github_client=self._gc,
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
                    content_sha=pf.content_sha,
                    embedding=pf.embedding.tobytes(),
                    updated_at=datetime.now(timezone.utc),
                ).on_conflict_do_update(
                    index_elements=["repo_full_name", "branch", "file_path"],
                    set_={
                        "file_sha": pf.item.get("sha"),
                        "file_size_bytes": pf.item.get("size"),
                        "content": pf.content,
                        "outline": pf.outline.structural_summary,
                        "content_sha": pf.content_sha,
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
        # Persist the fresh ETag so subsequent polls can cash in on 304.
        if new_tree_etag:
            meta.tree_etag = new_tree_etag
        await self._db.commit()
        # Cache safety: any file added/changed/removed invalidates curated
        # retrievals keyed on (repo, branch, query, …). The head_unchanged
        # short-circuit above and the no-diff early-return (line ~1253) both
        # skip this path, so this flush only fires when state actually moved.
        invalidate_curated_cache()

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

# ---------------------------------------------------------------------------
# Backward-compat re-exports
# ---------------------------------------------------------------------------
# These names were part of the public surface before the split and must
# remain importable from ``app.services.repo_index_service``. The query
# module is the new source of truth; this module re-exports so existing
# call sites (routers, tests, other services) don't have to change.
__all__ = [
    # Indexing-side public API
    "RepoIndexService",
    "FileOutline",
    "ProcessedFile",
    "invalidate_file_cache",
    # Re-exported from repo_index_query
    "CuratedCodebaseContext",
    "RepoIndexQuery",
    "invalidate_curated_cache",
    # Internal helpers imported by tests
    "_build_content_sha",
    "_classify_github_error",
    "_classify_source_type",
    "_compute_source_weight",
    "_curated_cache",
    "_extract_import_paths",
    "_extract_markdown_references",
    "_extract_structured_outline",
    "_publish_phase_change",
]
