"""Stage 3: Optimize (streaming)

Rewrites the prompt using the selected strategy.
Uses claude-opus for creative rewriting at maximum capability.
Streams token by token via SSE step_progress events.
"""

import asyncio
import json
import logging
import re
from typing import AsyncGenerator, Optional

from app.config import settings
from app.prompts.optimizer_prompts import get_optimizer_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider, parse_json_robust
from app.schemas.pipeline_outputs import OptimizeFallbackOutput
from app.services.context_builders import (
    build_analysis_summary,
    build_codebase_summary,
    build_strategy_summary,
    format_file_contexts,
    format_instructions,
    format_url_contexts,
)
from app.services.stage_runner import stream_with_timeout

logger = logging.getLogger(__name__)

# ── Intent-specific weaving guidance ──────────────────────────────────
# Maps explore intent_category to positive instructions telling the
# optimizer HOW to integrate codebase intelligence into the final prompt.
_WEAVING_GUIDANCE: dict[str, str] = {
    "refactoring": (
        "- Provide architectural context (module boundaries, data flow, dependency direction)\n"
        "  that helps the executor discover refactoring opportunities independently\n"
        "- Frame scope zones as structural observations ('X is defined in Y, also used in Z'),\n"
        "  not diagnoses ('X is wrong because Y') — let the executor draw conclusions\n"
        "- Preserve discovery: note that listed zones are highest-signal starting points,\n"
        "  not an exhaustive list — the executor may find additional opportunities\n"
        "- Use coverage % and test file counts to calibrate effort estimates\n"
        "- Extract architectural constraints from project docs and make them explicit"
    ),
    "api_design": (
        "- Use endpoint observations to define the API surface\n"
        "- Reference data contracts and integration points as explicit interface requirements"
    ),
    "feature_build": (
        "- Reference existing patterns the executor should follow\n"
        "- Name extension points and module boundaries"
    ),
    "testing": (
        "- Use coverage signals and testability observations to scope what needs testing\n"
        "- Reference mock patterns and test infrastructure"
    ),
    "debugging": (
        "- Map error paths and state mutations into a structured investigation plan\n"
        "- Reference specific functions and their behavioral characteristics"
    ),
    "architecture_review": (
        "- Use dependency and coupling observations to define review dimensions\n"
        "- Reference layer boundaries and cross-cutting concerns as review scope\n"
        "- Preserve discovery: listed areas are navigational context, not the complete\n"
        "  set of findings — the executor may identify additional architectural concerns"
    ),
    "performance": (
        "- Reference hot paths, I/O boundaries, and caching patterns as profiling targets"
    ),
    "security": (
        "- Map auth flows and credential handling into explicit review scope\n"
        "- Reference input validation patterns and encryption usage"
    ),
}
_DEFAULT_WEAVING = (
    "- Use file paths, function names, and data shapes to make instructions precise\n"
    "- Let codebase specifics inform the precision of your instructions"
)

OPTIMIZATION_META_OPEN = "<optimization_meta>"
OPTIMIZATION_META_CLOSE = "</optimization_meta>"


    # ── Preamble detection ─────────────────────────────────────────────
    # LLMs sometimes emit self-referential reasoning ("Let me read…",
    # "I have enough…", "Here is the optimized prompt:") before the
    # actual prompt text.  This pattern detects and strips such preamble
    # so neither the streamed display nor the stored prompt is polluted.

_PREAMBLE_PHRASES = re.compile(
    r"(?:^|\n)\s*(?:"
    r"Let me |I'll |I have |I need |I will |I should |I can see |"
    r"Here is |Here's |Based on |Looking at |Now I |Now let me |"
    r"Ok(?:ay)?,? |Alright,? |First,? let me |"
    r"I've (?:read|reviewed|analyzed|examined|looked)|"
    r"After (?:reading|reviewing|analyzing)|"
    r"Having (?:read|reviewed|analyzed)"
    r")",
    re.IGNORECASE,
)
_PREAMBLE_MAX_CHARS = 600  # Don't scan beyond this for preamble


def _strip_preamble(text: str) -> str:
    """Remove LLM reasoning preamble from the start of optimizer output.

    Detects self-referential reasoning paragraphs before the actual prompt
    and strips them.  Only operates on the first ~600 chars to avoid
    false positives deep inside a legitimate prompt.
    """
    if not text:
        return text

    # Look for a double-newline boundary in the first 600 chars
    scan_zone = text[:_PREAMBLE_MAX_CHARS]
    split_pos = scan_zone.find("\n\n")

    if split_pos == -1:
        # No paragraph break — check if the ENTIRE text starts with preamble
        # (single-line reasoning like "Let me produce it now. Audit all...")
        # Look for a sentence-ending period followed by a capital letter
        m = re.match(
            r"^((?:Let me|I'll|I have|I need|I will|Here is|Here's|Now )[^.]*\.)\s*",
            text,
            re.IGNORECASE,
        )
        if m:
            stripped = text[m.end():]
            logger.info("Stripped single-line preamble (%d chars)", m.end())
            return stripped  # May be empty during streaming — that's correct
        return text

    first_para = scan_zone[:split_pos].strip()

    # Check if the first paragraph looks like LLM reasoning
    if _PREAMBLE_PHRASES.search(first_para):
        stripped = text[split_pos:].lstrip("\n")
        logger.info(
            "Stripped preamble paragraph (%d chars): %r",
            split_pos,
            first_para[:100],
        )
        return stripped  # May be empty during streaming — that's correct

    return text


class OptimizeStreamParser:
    """Separates prompt text from metadata during streaming.

    Buffers chunks, yields only clean prompt text for SSE display.
    The <optimization_meta> block is silently accumulated and extracted
    after streaming completes via finalize().

    Handles: cross-boundary markers, pure-JSON fallback, partial timeout,
    and LLM preamble stripping.
    """

    def __init__(self) -> None:
        self._buffer: str = ""
        self._full_text: str = ""
        self._prompt_text: str = ""
        self._meta_text: str = ""
        self._in_meta: bool = False
        self._marker_len: int = len(OPTIMIZATION_META_OPEN)
        # Preamble detection: buffer the first paragraph before yielding
        self._preamble_checked: bool = False
        self._preamble_buf: str = ""

    def feed(self, chunk: str) -> str:
        """Feed a streamed chunk. Returns text safe to yield to the user.

        Returns empty string when buffering (potential partial marker,
        preamble detection, or metadata section).
        """
        self._full_text += chunk

        if self._in_meta:
            self._meta_text += chunk
            return ""

        # Phase 1: Buffer initial text for preamble detection.
        # Wait for a double newline (paragraph boundary) or 600 chars
        # before deciding whether to strip.
        if not self._preamble_checked:
            self._preamble_buf += chunk

            has_para_break = "\n\n" in self._preamble_buf
            over_limit = len(self._preamble_buf) > _PREAMBLE_MAX_CHARS

            if has_para_break or over_limit:
                self._preamble_checked = True
                cleaned = _strip_preamble(self._preamble_buf)
                self._buffer = cleaned
                # Fall through to normal marker detection below
            else:
                return ""  # Still buffering for preamble check

        else:
            self._buffer += chunk

        # Phase 2: Normal marker detection (existing logic)
        marker_pos = self._buffer.find(OPTIMIZATION_META_OPEN)
        if marker_pos != -1:
            safe_text = self._buffer[:marker_pos]
            self._meta_text = self._buffer[marker_pos + self._marker_len :]
            self._buffer = ""
            self._in_meta = True
            self._prompt_text += safe_text
            return safe_text

        # Keep safety margin for cross-boundary marker detection
        safety_margin = self._marker_len - 1
        if len(self._buffer) > safety_margin:
            safe_text = self._buffer[:-safety_margin]
            self._buffer = self._buffer[-safety_margin:]
            self._prompt_text += safe_text
            return safe_text

        return ""

    def finalize(self) -> tuple[str, dict | None]:
        """Extract (prompt_text, metadata_dict) after streaming completes.

        Three outcomes:
        1. Marker found + metadata parsed → (prompt, metadata_dict)
        2. No marker, JSON fallback → (optimized_prompt from JSON, full_dict)
        3. All parsing fails → (full_text, None)
        """
        # Flush any un-checked preamble buffer (short outputs that never
        # hit the paragraph-break threshold).  Run marker detection inline
        # (not via feed(), which would double-count _full_text).
        if not self._preamble_checked and self._preamble_buf:
            self._preamble_checked = True
            cleaned = _strip_preamble(self._preamble_buf)
            self._preamble_buf = ""
            if cleaned:
                # Check for metadata marker in the cleaned text
                marker_pos = cleaned.find(OPTIMIZATION_META_OPEN)
                if marker_pos != -1:
                    self._prompt_text += cleaned[:marker_pos]
                    self._meta_text = cleaned[marker_pos + self._marker_len :]
                    self._in_meta = True
                else:
                    self._buffer += cleaned

        if not self._in_meta and self._buffer:
            self._prompt_text += self._buffer
            self._buffer = ""

        prompt = self._prompt_text.strip()

        if self._in_meta:
            meta_str = self._meta_text
            close_pos = meta_str.find(OPTIMIZATION_META_CLOSE)
            if close_pos != -1:
                meta_str = meta_str[:close_pos]
            meta_str = meta_str.strip()
            if meta_str:
                try:
                    return prompt, json.loads(meta_str)
                except json.JSONDecodeError:
                    logger.warning("Metadata JSON parse failed: %r", meta_str[:200])
            return prompt, None

        # No marker → try JSON fallback (backward compat)
        try:
            parsed = parse_json_robust(self._full_text)
            return parsed.get("optimized_prompt", self._full_text), parsed
        except ValueError:
            return prompt or self._full_text.strip(), None

    @property
    def accumulated_prompt(self) -> str:
        """Prompt text accumulated so far (for partial-timeout recovery)."""
        return self._prompt_text + self._buffer


async def run_optimize(
    provider: LLMProvider,
    raw_prompt: str,
    analysis: dict,
    strategy: dict,
    codebase_context: Optional[dict] = None,
    retry_constraints: Optional[dict] = None,
    file_contexts: list[dict] | None = None,        # N24: attached file content
    instructions: list[str] | None = None,          # N25: user output constraints
    url_fetched_contexts: list[dict] | None = None, # N26: pre-fetched URL content
    model: str | None = None,
    streaming: bool = True,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 3 optimization with streaming.

    Yields:
        ("step_progress", {"step": "optimize", "content": "chunk"}) for each token
        ("optimization", {optimized_prompt, changes_made, framework_applied, optimization_notes})

    Args:
        retry_constraints: If provided, includes adjusted constraints for retry attempts
            with keys: min_score_target, previous_score, focus_areas
    """
    task_type = analysis.get("task_type", "general")
    system_prompt = get_optimizer_prompt(task_type)

    analysis_summary = build_analysis_summary(analysis)
    strategy_summary = build_strategy_summary(strategy)

    user_message = (
        f"Raw prompt to optimize:\n---\n{raw_prompt}\n---\n\n"
        f"Analysis:\n{analysis_summary}\n\n"
        f"Strategy:\n{strategy_summary}"
    )
    if codebase_context:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            intent_cat = codebase_context.get("intent_category", "general")
            coverage = codebase_context.get("coverage_pct", 0)
            files_read = codebase_context.get("files_read_count", 0)
            weaving = _WEAVING_GUIDANCE.get(intent_cat, _DEFAULT_WEAVING)

            user_message += (
                "\n\n--- Codebase reference (INTELLIGENCE LAYER — for YOUR understanding only) ---\n"
                f"Intent focus: {intent_cat} · Coverage: {coverage}% · {files_read} files\n\n"
                "Conciseness calibration: this reference is rich with specifics. Absorb them\n"
                "into precise instructions — do NOT expand the prompt proportionally. Every\n"
                "file path or function name should REPLACE a vague instruction, not supplement\n"
                "it. Fold constraints into the section they govern (output rules into the output\n"
                "format, ranking rules into the scope header) — do NOT create separate sections\n"
                "that restate requirements already embedded elsewhere.\n\n"
                "Weaving guidance (how to USE this context in the optimized prompt):\n"
                f"{weaving}\n\n"
                "Guardrails:\n"
                "- Do NOT relay exploration findings, observations, or context notes\n"
                "- Do NOT add 'Codebase Context' or 'Background' sections\n"
                "- Do NOT treat observations marked [unverified] as fact\n"
                "- Do NOT delegate investigation tasks to the executor\n"
                "- Do NOT invent specifics beyond what appears below\n\n"
                f"{codebase_summary}\n"
                "--- End codebase reference ---"
            )

    # N24: inject attached file content
    user_message += format_file_contexts(file_contexts)

    # N26: inject pre-fetched URL content
    user_message += format_url_contexts(url_fetched_contexts)

    if retry_constraints:
        user_message += (
            f"\n\n--- RETRY WITH ADJUSTED CONSTRAINTS ---\n"
            f"This is retry attempt {retry_constraints.get('retry_attempt', 1)}.\n"
            f"Previous optimization scored {retry_constraints.get('previous_score', 'low')}/10.\n"
            f"Target minimum score: {retry_constraints.get('min_score_target', 7)}/10.\n"
            f"Focus on improving these issues: {json.dumps(retry_constraints.get('focus_areas', []))}\n"
            f"Be MORE specific, structured, and detailed than the previous attempt.\n"
            f"Ensure the optimized prompt is substantially better than the original."
        )

    # N25: prepend instruction constraints so they take highest priority
    instr_block = format_instructions(
        instructions, label="User-specified output constraints (MUST follow)"
    )
    if instr_block:
        user_message = instr_block.lstrip("\n") + "\n\n" + user_message

    model = model or MODEL_ROUTING["optimize"]
    framework_applied = strategy.get("primary_framework", "")

    full_text = ""
    parser: OptimizeStreamParser | None = None

    if not streaming:
        # Non-streaming mode: single complete() call, no step_progress events.
        try:
            full_text = await asyncio.wait_for(
                provider.complete(system_prompt, user_message, model),
                timeout=settings.OPTIMIZE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.error("Stage 3 (Optimize) non-streaming complete() failed: %s", e)
            full_text = ""
        # Parse using same marker logic as streaming path
        parser = OptimizeStreamParser()
        parser.feed(full_text)
    else:
        stream_failed = False
        parser = OptimizeStreamParser()
        async for status, text in stream_with_timeout(
            provider, system_prompt, user_message, model,
            settings.OPTIMIZE_TIMEOUT_SECONDS, "Stage 3 (Optimize)",
        ):
            if status == "chunk":
                full_text += text  # type: ignore[operator]
                safe_text = parser.feed(text)  # type: ignore[arg-type]
                if safe_text:
                    yield ("step_progress", {"step": "optimize", "content": safe_text})
            elif status == "done":
                pass  # full_text already accumulated
            elif status == "timeout":
                if not full_text:
                    raise asyncio.CancelledError()
                # Partial text accumulated; fall through to extraction
            elif status == "error":
                stream_failed = True

        if stream_failed:
            # Non-streaming fallback when streaming itself errors out
            try:
                full_text = await asyncio.wait_for(
                    provider.complete(system_prompt, user_message, model),
                    timeout=settings.OPTIMIZE_TIMEOUT_SECONDS,
                )
            except Exception as e2:
                logger.error("Stage 3 (Optimize) complete() also failed: %s", e2)
                full_text = ""
            # Re-parse with fresh parser since stream parser state is invalid
            parser = OptimizeStreamParser()
            parser.feed(full_text)

    # Extract prompt + metadata via parser (handles both marker and JSON fallback)
    if parser is None:
        parser = OptimizeStreamParser()
        parser.feed(full_text)

    prompt_text, metadata = parser.finalize()

    optimization_failed = False
    if metadata and isinstance(metadata, dict):
        optimized_prompt = (
            metadata.get("optimized_prompt", prompt_text)
            if "optimized_prompt" in metadata
            else prompt_text
        )
        changes_made = metadata.get("changes_made", [])
        framework_applied = metadata.get("framework_applied", framework_applied)
        optimization_notes = metadata.get("optimization_notes", "")
    elif prompt_text:
        optimized_prompt = prompt_text
        changes_made = []
        optimization_notes = ""
        logger.warning("Optimize stage: metadata extraction failed; using prompt text only")
    elif full_text:
        # Last resort: try complete_json() fallback
        logger.warning("No prompt text or metadata; trying complete_parsed() fallback")
        try:
            fallback_obj = await asyncio.wait_for(
                provider.complete_parsed(
                    system_prompt, user_message, model, OptimizeFallbackOutput,
                ),
                timeout=settings.OPTIMIZE_TIMEOUT_SECONDS,
            )
            optimized_prompt = fallback_obj.optimized_prompt
            changes_made = fallback_obj.changes_made
            framework_applied = fallback_obj.framework_applied or framework_applied
            optimization_notes = fallback_obj.optimization_notes
        except Exception as e:
            logger.error("complete_parsed() fallback also failed: %s", e)
            optimized_prompt = full_text
            changes_made = []
            optimization_notes = ""
    else:
        optimization_failed = True
        optimized_prompt = ""
        changes_made = []
        optimization_notes = ""

    yield ("optimization", {
        "optimized_prompt": optimized_prompt,
        "changes_made": changes_made,
        "framework_applied": framework_applied,
        "optimization_notes": optimization_notes,
        "optimization_failed": optimization_failed,
    })
