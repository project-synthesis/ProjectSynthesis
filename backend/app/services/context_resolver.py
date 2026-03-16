"""Context resolver: validates and caps all context sources for an optimization request."""

import logging
import uuid

from app.config import settings
from app.schemas.pipeline_contracts import ResolvedContext

logger = logging.getLogger(__name__)

MIN_PROMPT_CHARS = 20


class ContextResolver:
    """Static class that resolves all context sources for an optimization request.

    Enforces per-source character caps, wraps external content in
    ``<untrusted-context>`` delimiters, and validates prompt length before
    returning a :class:`~app.schemas.pipeline_contracts.ResolvedContext`.
    """

    @staticmethod
    def resolve(
        raw_prompt: str,
        strategy_override: str | None = None,
        codebase_guidance: str | None = None,
        codebase_context: str | None = None,
        adaptation_state: str | None = None,
        workspace_path: str | None = None,
    ) -> ResolvedContext:
        """Resolve and sanitise all context sources.

        Args:
            raw_prompt: The user-supplied prompt to optimise.
            strategy_override: Optional explicit strategy name.
            codebase_guidance: Optional codebase guidance text (e.g. from repo
                analysis).  Truncated to ``MAX_GUIDANCE_CHARS`` and wrapped in
                ``<untrusted-context source="codebase-guidance">``.
            codebase_context: Optional GitHub explore output.  Truncated to
                ``MAX_CODEBASE_CONTEXT_CHARS`` and wrapped in
                ``<untrusted-context source="github-explore">``.
            adaptation_state: Optional serialised adaptation state.  Truncated
                to ``MAX_ADAPTATION_CHARS``; no wrapping applied.
            workspace_path: Optional filesystem path to a local workspace.
                When ``codebase_guidance`` is ``None`` and a path is given,
                the workspace is scanned for guidance files (e.g. ``CLAUDE.md``)
                via :class:`~app.services.roots_scanner.RootsScanner`.

        Returns:
            A fully-resolved :class:`ResolvedContext` with a fresh trace_id.

        Raises:
            ValueError: If ``raw_prompt`` is shorter than ``MIN_PROMPT_CHARS``
                or longer than ``settings.MAX_RAW_PROMPT_CHARS``.
        """
        # --- Prompt length validation ---
        if len(raw_prompt) < MIN_PROMPT_CHARS:
            raise ValueError(
                f"Prompt too short ({len(raw_prompt)} chars). "
                f"Minimum is {MIN_PROMPT_CHARS} characters."
            )
        if len(raw_prompt) > settings.MAX_RAW_PROMPT_CHARS:
            raise ValueError(
                f"Prompt exceeds maximum length ({len(raw_prompt)} chars). "
                f"Maximum is {settings.MAX_RAW_PROMPT_CHARS} characters."
            )

        # --- Auto-scan workspace if no explicit guidance provided ---
        if codebase_guidance is None and workspace_path:
            from pathlib import Path

            from app.services.roots_scanner import RootsScanner

            scanner = RootsScanner()
            codebase_guidance = scanner.scan(Path(workspace_path))

        # --- Per-source cap + injection hardening ---
        if codebase_guidance is not None:
            orig_len = len(codebase_guidance)
            # Skip wrapping if already wrapped by roots scanner
            if "<untrusted-context" not in codebase_guidance:
                codebase_guidance = codebase_guidance[: settings.MAX_GUIDANCE_CHARS]
                if len(codebase_guidance) < orig_len:
                    logger.info(
                        "Truncated codebase_guidance from %d to %d chars",
                        orig_len, settings.MAX_GUIDANCE_CHARS,
                    )
                codebase_guidance = (
                    f'<untrusted-context source="codebase-guidance">\n'
                    f"{codebase_guidance}\n"
                    f"</untrusted-context>"
                )
            else:
                # Already wrapped by scanner — just enforce total cap
                cap = settings.MAX_GUIDANCE_CHARS + 200  # +200 for tag overhead
                codebase_guidance = codebase_guidance[:cap]
                if len(codebase_guidance) < orig_len:
                    logger.info("Truncated pre-wrapped codebase_guidance from %d to %d chars", orig_len, cap)

        if codebase_context is not None:
            orig_len = len(codebase_context)
            codebase_context = codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]
            if len(codebase_context) < orig_len:
                logger.info(
                    "Truncated codebase_context from %d to %d chars",
                    orig_len, settings.MAX_CODEBASE_CONTEXT_CHARS,
                )
            codebase_context = (
                f'<untrusted-context source="github-explore">\n'
                f"{codebase_context}\n"
                f"</untrusted-context>"
            )

        if adaptation_state is not None:
            orig_len = len(adaptation_state)
            adaptation_state = adaptation_state[: settings.MAX_ADAPTATION_CHARS]
            if len(adaptation_state) < orig_len:
                logger.info(
                    "Truncated adaptation_state from %d to %d chars",
                    orig_len, settings.MAX_ADAPTATION_CHARS,
                )

        context_sources: dict[str, bool] = {
            "codebase_guidance": codebase_guidance is not None,
            "codebase_context": codebase_context is not None,
            "adaptation": adaptation_state is not None,
        }

        trace_id = str(uuid.uuid4())

        logger.debug(
            "ContextResolver resolved trace_id=%s sources=%s prompt_len=%d",
            trace_id,
            context_sources,
            len(raw_prompt),
        )

        return ResolvedContext(
            raw_prompt=raw_prompt,
            strategy_override=strategy_override,
            codebase_guidance=codebase_guidance,
            codebase_context=codebase_context,
            adaptation_state=adaptation_state,
            context_sources=context_sources,
            trace_id=trace_id,
        )
