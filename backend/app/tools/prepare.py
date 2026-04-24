"""Handler for synthesis_prepare_optimization MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from mcp.server.fastmcp import Context

from app.config import PROMPTS_DIR
from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import PrepareOutput
from app.services.passthrough import assemble_passthrough_prompt
from app.services.preferences import PreferencesService
from app.services.project_service import resolve_repo_project
from app.tools._shared import DATA_DIR, auto_resolve_repo, get_context_service, get_routing

logger = logging.getLogger(__name__)


def _get_provider_safe():
    try:
        return get_routing().state.provider
    except ValueError:
        return None


async def handle_prepare(
    prompt: str,
    strategy: str | None,
    max_context_tokens: int,
    workspace_path: str | None,
    repo_full_name: str | None,
    ctx: Context | None,
) -> PrepareOutput:
    """Assemble the full optimization prompt with context for an external LLM."""
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )
    if max_context_tokens < 1:
        raise ValueError(
            "max_context_tokens must be a positive integer (got %d)" % max_context_tokens
        )

    # Validate workspace_path — reject obviously unsafe paths
    if workspace_path:
        wp = Path(workspace_path).resolve()
        # Block system directories and common sensitive paths
        _blocked_prefixes = ("/etc", "/var", "/proc", "/sys", "/root", "/boot", "/dev")
        if any(str(wp).startswith(prefix) for prefix in _blocked_prefixes):
            raise ValueError(
                "workspace_path '%s' points to a system directory. "
                "Only user workspace directories are allowed." % workspace_path
            )

    # ---- Auto-resolve repo from linked repo if not provided ----
    effective_repo = await auto_resolve_repo(repo_full_name)

    # Resolve strategy: explicit param → user preference → auto
    prefs = PreferencesService(DATA_DIR)
    effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"

    logger.info(
        "synthesis_prepare_optimization called: prompt_len=%d strategy=%s",
        len(prompt), effective_strategy,
    )

    # Unified context enrichment — resolves guidance, adaptation, analysis, patterns
    from app.config import PROJECT_ROOT
    effective_workspace = workspace_path or str(PROJECT_ROOT)

    # B1/B7: freeze project_id BEFORE enrichment so pattern scoping honors it.
    _, _prep_project_id = await resolve_repo_project(effective_repo)

    context_service = get_context_service()
    async with async_session_factory() as enrich_db:
        enrichment = await context_service.enrich(
            raw_prompt=prompt,
            tier="passthrough",
            db=enrich_db,
            workspace_path=effective_workspace,
            mcp_ctx=ctx,
            repo_full_name=effective_repo,
            project_id=_prep_project_id,
            provider=_get_provider_safe(),
        )

    # Few-shot retrieval for passthrough (parity with internal/sampling)
    _pt_few_shot: str | None = None
    try:
        from app.services.pattern_injection import (
            format_few_shot_examples,
            retrieve_few_shot_examples,
        )
        async with async_session_factory() as _fs_db:
            _fs_examples = await retrieve_few_shot_examples(
                raw_prompt=prompt, db=_fs_db, trace_id=str(uuid.uuid4()),
            )
        _pt_few_shot = format_few_shot_examples(_fs_examples)
    except Exception:
        logger.debug("Prepare few-shot retrieval failed (non-fatal)")

    assembled, strategy_name = assemble_passthrough_prompt(
        prompts_dir=PROMPTS_DIR,
        raw_prompt=prompt,
        strategy_name=effective_strategy,
        strategy_intelligence=enrichment.strategy_intelligence,
        analysis_summary=enrichment.analysis_summary,
        codebase_context=enrichment.codebase_context,
        applied_patterns=enrichment.applied_patterns,
        divergence_alerts=enrichment.divergence_alerts,
        few_shot_examples=_pt_few_shot,
    )

    # Enforce max_context_tokens budget
    estimated_tokens = len(assembled) // 4
    was_truncated = estimated_tokens > max_context_tokens
    if was_truncated:
        max_chars = max_context_tokens * 4
        assembled = assembled[:max_chars]
        context_size_tokens = max_context_tokens
    else:
        context_size_tokens = estimated_tokens

    trace_id = str(uuid.uuid4())

    # Store pending optimization with raw_prompt for later save_result linkage
    async with async_session_factory() as db:
        pending = Optimization(
            id=str(uuid.uuid4()),
            raw_prompt=prompt,
            status="pending",
            trace_id=trace_id,
            provider="mcp_passthrough",
            routing_tier="passthrough",
            strategy_used=strategy_name,
            task_type=enrichment.task_type,
            domain=enrichment.domain_value,
            domain_raw=enrichment.domain_value,
            intent_label=enrichment.intent_label,
            repo_full_name=effective_repo,
            project_id=_prep_project_id,
            context_sources=enrichment.context_sources_dict,
        )
        db.add(pending)
        await db.commit()

    logger.info(
        "synthesis_prepare_optimization completed: trace_id=%s strategy=%s tokens=%d",
        trace_id, strategy_name, context_size_tokens,
    )

    return PrepareOutput(
        trace_id=trace_id,
        assembled_prompt=assembled,
        context_size_tokens=context_size_tokens,
        strategy_requested=strategy_name,
        was_truncated=was_truncated,
    )
