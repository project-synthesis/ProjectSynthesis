"""Codebase explorer: semantic retrieval + single-shot synthesis.

Fetches relevant files from a GitHub repo, ranks them by semantic
similarity to the user prompt, and synthesizes a structured context
summary via a Haiku LLM call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.embedding_service import EmbeddingService
from app.services.explore_cache import ExploreCache
from app.services.github_client import GitHubClient
from app.services.prompt_loader import PromptLoader

if TYPE_CHECKING:
    from app.services.repo_index_service import RepoIndexService

logger = logging.getLogger(__name__)

# File extensions worth reading (code and config)
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini",
    ".html", ".css", ".scss", ".svelte", ".vue",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql",
}

# Module-level cache singleton — shared across explorer instances
_explore_cache = ExploreCache(ttl_seconds=settings.EXPLORE_RESULT_CACHE_TTL)



class ExploreOutput(BaseModel):
    """Structured output from the explore synthesis LLM call."""

    model_config = ConfigDict(extra="forbid")
    context: str = Field(description="Synthesized codebase context summary for injection into the optimizer.")


class CodebaseExplorer:
    """Explores a GitHub repo to provide codebase context for prompt optimization.

    Flow:
    1. Fetch repo tree via GitHub API
    2. Rank files by semantic similarity (embedding) or keyword fallback
    3. Parallel-read top files with line-budget allocation
    4. Render explore.md template
    5. Single-shot LLM synthesis (Haiku, no thinking)
    6. Return context string or None on error
    """

    def __init__(
        self,
        prompt_loader: PromptLoader,
        github_client: GitHubClient,
        embedding_service: EmbeddingService,
        provider: LLMProvider,
        repo_index_service: RepoIndexService | None = None,
    ) -> None:
        self._loader = prompt_loader
        self._gc = github_client
        self._es = embedding_service
        self._provider = provider
        self._ris = repo_index_service

    async def explore(
        self,
        raw_prompt: str,
        repo_full_name: str,
        branch: str,
        token: str,
    ) -> str | None:
        """Run explore pipeline: fetch files, rank, read, synthesize.

        Returns the synthesized context string, or None on any error
        (non-fatal — optimization proceeds without codebase context).
        """
        try:
            return await self._explore_inner(raw_prompt, repo_full_name, branch, token)
        except Exception:
            logger.exception(
                "Explore failed for %s@%s — proceeding without codebase context",
                repo_full_name,
                branch,
            )
            return None

    async def _explore_inner(
        self,
        raw_prompt: str,
        repo_full_name: str,
        branch: str,
        token: str,
    ) -> str:
        """Inner explore logic (exceptions propagate to caller)."""
        # 1. Get HEAD SHA + file tree
        head_sha, tree = await asyncio.gather(
            self._gc.get_branch_head_sha(token, repo_full_name, branch),
            self._gc.get_tree(token, repo_full_name, branch),
        )

        logger.info(
            "Explore: %s@%s HEAD=%s, %d files in tree",
            repo_full_name, branch, head_sha[:8], len(tree),
        )

        # Check cache before running full pipeline
        cache_key = _explore_cache.build_key(repo_full_name, branch, head_sha, raw_prompt)
        cached = _explore_cache.get(cache_key)
        if cached is not None:
            logger.info("Explore cache hit for %s@%s (SHA=%s)", repo_full_name, branch, head_sha[:8])
            return cached

        # 2. Filter to indexable files
        indexable = [
            item for item in tree
            if _is_code_file(item["path"])
        ]

        # 3. Rank files: semantic search or keyword fallback
        ranked_paths = await self._rank_files(raw_prompt, indexable, repo_full_name, branch)

        # 4. Cap at EXPLORE_MAX_FILES
        max_files = settings.EXPLORE_MAX_FILES
        selected_paths = ranked_paths[:max_files]
        logger.info(
            "explore_rank: repo=%s tree=%d indexable=%d selected=%d top5=%s",
            repo_full_name, len(tree), len(indexable), len(selected_paths),
            selected_paths[:5],
        )

        # 5. Parallel file reads with bounded concurrency
        semaphore = asyncio.Semaphore(10)
        read_results = await asyncio.gather(
            *[
                self._read_file(semaphore, token, repo_full_name, branch, path)
                for path in selected_paths
            ]
        )

        # 6. Allocate line budget and build file contents
        line_budget = settings.EXPLORE_TOTAL_LINE_BUDGET
        per_file_budget = line_budget // max(len(selected_paths), 1)

        file_paths_list = []
        file_contents_parts = []
        total_chars = 0

        for path, content in read_results:
            if content is None:
                continue

            # Truncate to per-file line budget
            lines = content.splitlines()
            if len(lines) > per_file_budget:
                lines = lines[:per_file_budget]
                truncated = True
            else:
                truncated = False

            # Add line numbers
            numbered = "\n".join(
                f"{i + 1:>5} {line}" for i, line in enumerate(lines)
            )

            # Build the file block
            block = f"--- {path} ---\n{numbered}"
            if truncated:
                block += f"\n[... truncated at {per_file_budget} lines]"

            # Context overflow guard
            if total_chars + len(block) > settings.EXPLORE_MAX_CONTEXT_CHARS:
                logger.info("Explore: context overflow at %d chars, stopping file reads", total_chars)
                break

            file_paths_list.append(path)
            file_contents_parts.append(block)
            total_chars += len(block)

        file_paths_str = "\n".join(file_paths_list)
        file_contents_str = "\n\n".join(file_contents_parts)

        # 7. Truncate raw_prompt for the template
        prompt_for_template = raw_prompt[:settings.EXPLORE_MAX_PROMPT_CHARS]

        # 8. Render explore.md template
        rendered = self._loader.render("explore.md", {
            "raw_prompt": prompt_for_template,
            "file_paths": file_paths_str,
            "file_contents": file_contents_str,
        })

        # 9. Single-shot synthesis via provider (with retry for transient errors)
        result: ExploreOutput = await call_provider_with_retry(
            self._provider,
            model=settings.MODEL_HAIKU,
            system_prompt=self._loader.load("explore-guidance.md"),
            user_message=rendered,
            output_format=ExploreOutput,
        )

        logger.info(
            "explore_synthesis: repo=%s branch=%s files_read=%d synthesis_chars=%d",
            repo_full_name, branch, len(file_paths_list), len(result.context),
        )

        # Cache the result
        _explore_cache.set(cache_key, result.context)

        return result.context

    async def _rank_files(
        self,
        raw_prompt: str,
        tree_items: list[dict],
        repo_full_name: str | None = None,
        branch: str | None = None,
    ) -> list[str]:
        """Rank files by semantic similarity using index embeddings when available.

        Fallback cascade:
        1. Query pre-computed RepoFileIndex embeddings (richer: path+content)
        2. Path-embed files missing from the index (added after last indexing)
        3. If no index or DB failure, path-embed all files (original behavior)
        4. If embedding entirely unavailable, keyword fallback
        """
        paths = [item["path"] for item in tree_items]
        t0 = time.monotonic()
        source = "keyword"  # tracks which path was taken for the final log

        try:
            query_vec = await self._es.aembed_single(raw_prompt)

            # Try index embeddings first
            index_vecs: dict[str, np.ndarray] = {}
            if self._ris and repo_full_name and branch:
                try:
                    t_idx = time.monotonic()
                    index_vecs = await self._ris.get_embeddings_by_paths(
                        repo_full_name, branch, paths,
                    )
                    logger.debug(
                        "rank_files: index lookup returned %d/%d embeddings in %.0fms",
                        len(index_vecs), len(paths),
                        (time.monotonic() - t_idx) * 1000,
                    )
                except Exception:
                    logger.warning(
                        "rank_files: index embedding lookup failed for %s@%s — "
                        "falling back to path embeddings",
                        repo_full_name, branch, exc_info=True,
                    )

            # Separate indexed vs unindexed paths
            if index_vecs:
                unindexed = [p for p in paths if p not in index_vecs]
                source = "index"
                # Path-embed only the missing files
                if unindexed:
                    source = "index+path"
                    t_fb = time.monotonic()
                    fallback_vecs = await self._es.aembed_texts(unindexed)
                    for p, v in zip(unindexed, fallback_vecs):
                        index_vecs[p] = v
                    logger.debug(
                        "rank_files: path-embedded %d unindexed files in %.0fms",
                        len(unindexed), (time.monotonic() - t_fb) * 1000,
                    )
                # Build corpus in original path order
                corpus_vecs = [index_vecs[p] for p in paths]
            else:
                # No index available — original path-embedding behavior
                source = "path"
                corpus_vecs = await self._es.aembed_texts(paths)

            ranked = self._es.cosine_search(query_vec, corpus_vecs, top_k=len(paths))
            elapsed_ms = (time.monotonic() - t0) * 1000
            # Log top scores so we can assess ranking quality
            top3 = [(paths[idx], round(score, 3)) for idx, score in ranked[:3]]
            logger.info(
                "rank_files: source=%s files=%d elapsed=%.0fms top3=%s",
                source, len(paths), elapsed_ms, top3,
            )
            return [paths[idx] for idx, _score in ranked]
        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                "rank_files: embedding failed after %.0fms — keyword fallback "
                "(files=%d)",
                elapsed_ms, len(paths), exc_info=True,
            )
            return self._keyword_rank(raw_prompt, paths)

    @staticmethod
    def _keyword_rank(query: str, paths: list[str]) -> list[str]:
        """Rank files by keyword overlap with the query.

        Scores each path by how many query tokens appear in the path
        (case-insensitive). Ties are broken by original order.
        """
        tokens = set(query.lower().split())

        def score(path: str) -> int:
            path_lower = path.lower()
            return sum(1 for t in tokens if t in path_lower)

        scored = [(path, score(path)) for path in paths]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [path for path, _ in scored]

    async def _read_file(
        self,
        semaphore: asyncio.Semaphore,
        token: str,
        repo_full_name: str,
        branch: str,
        path: str,
    ) -> tuple[str, str | None]:
        """Read a single file with bounded concurrency."""
        async with semaphore:
            try:
                content = await self._gc.get_file_content(
                    token, repo_full_name, path, branch
                )
                return path, content
            except Exception:
                logger.warning("Failed to read %s from %s@%s", path, repo_full_name, branch)
                return path, None


def _is_code_file(path: str) -> bool:
    """Return True if the file extension is worth reading."""
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in _CODE_EXTENSIONS
