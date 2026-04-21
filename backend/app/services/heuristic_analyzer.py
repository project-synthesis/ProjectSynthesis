"""Zero-LLM heuristic prompt analyzer (orchestrator).

Classifies task_type, domain, detects weaknesses/strengths, and recommends
a strategy — almost entirely without LLM calls.  The confidence-gated A4
Haiku fallback (``classify_with_llm``) handles the ambiguous ~15-20% of
prompts.  Designed for passthrough tier enrichment where we cannot call
external models.

This file is the **thin orchestrator** over three cohesive sub-modules
(Phase 3F of the code-quality sweep):

* ``task_type_classifier`` — A1 compound keywords, A2 technical verb+noun
  disambiguation, A4 confidence-gated LLM fallback, signal pattern cache.
* ``domain_detector`` — DomainSignalLoader delegation + organic-vocabulary
  sub-qualifier enrichment.
* ``weakness_detector`` — structural weakness / strength surfacing.

Public API is preserved via re-exports: every external caller that
imported ``HeuristicAnalyzer`` / ``HeuristicAnalysis`` /
``set_signal_loader`` / ``set_task_type_signals`` / ``_enrich_domain_qualifier``
from this module continues to work unchanged.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.domain_detector import (
    classify_domain as _classify_domain,
)
from app.services.domain_detector import (
    get_signal_loader,
    set_signal_loader,
)
from app.services.task_type_classifier import (
    check_technical_disambiguation,
    classify_task_type,
    classify_with_llm,
    get_task_type_signals,
    score_category,
    set_task_type_signals,
    task_type_has_dynamic_signals,
)
from app.services.task_type_classifier import (
    LLM_CLASSIFICATION_CONFIDENCE_GATE as _LLM_CLASSIFICATION_CONFIDENCE_GATE,
)
from app.services.task_type_classifier import (
    LLM_CLASSIFICATION_MARGIN_GATE as _LLM_CLASSIFICATION_MARGIN_GATE,
)
from app.services.weakness_detector import (
    detect_strengths,
    detect_weaknesses,
)
from app.services.weakness_detector import (
    has_code_blocks as _has_code_blocks,
)
from app.services.weakness_detector import (
    has_markdown_lists as _has_markdown_lists,
)
from app.utils.text_cleanup import LABEL_STOP_WORDS, extract_meaningful_words

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent label helpers (module-level for reuse and testability)
# ---------------------------------------------------------------------------

_COMMON_VERBS = frozenset({
    # Original 25
    "implement", "create", "build", "write", "design", "refactor",
    "fix", "add", "remove", "update", "migrate", "deploy", "test",
    "analyze", "review", "evaluate", "compare", "draft", "generate",
    "configure", "optimize", "debug", "integrate", "setup", "improve",
    # Extended — common in technical and creative prompts
    "transform", "convert", "parse", "validate", "document", "scaffold",
    "extract", "summarize", "clean", "format", "check", "verify",
    "sort", "merge", "split", "translate", "explain", "simplify",
    "define", "list", "calculate", "monitor", "render", "serialize",
    "fetch", "handle", "process", "wrap", "encode", "decode",
})


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeuristicAnalysis:
    """Result of heuristic prompt analysis."""

    task_type: str       # coding | writing | analysis | creative | data | system | general
    domain: str          # from domain nodes: backend | frontend | database | devops | security | general | discovered
    intent_label: str    # 3-6 word phrase
    weaknesses: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    recommended_strategy: str = "auto"
    confidence: float = 0.0
    # Disambiguation tracking (set when A2 technical verb+noun override fires)
    disambiguation_applied: bool = False
    disambiguation_from: str | None = None
    # Domain signal scores (A3 observability — which domains matched and with what weight)
    domain_scores: dict[str, float] | None = None
    # LLM fallback tracking (set when A4 confidence-gated LLM classification fires)
    llm_fallback_applied: bool = False
    # Task-type signal source tracking.
    # "dynamic"   → current-run TF-IDF extraction crossed the sample threshold
    # "bootstrap" → signals come from the static compound table and/or
    #               cache-warmup load (A4: the legacy "static" label is
    #               accepted as a synonym for read-compat).
    task_type_signal_source: str = "bootstrap"
    task_type_scores: dict[str, float] | None = None  # raw scores per task type

    def format_summary(self) -> str:
        """Format analysis as a human-readable string for template injection."""
        parts = [
            f"Task type: {self.task_type}",
            f"Domain: {self.domain}",
            f"Intent: {self.intent_label}",
        ]
        if self.weaknesses:
            parts.append("Weaknesses:")
            for w in self.weaknesses:
                parts.append(f"- {w}")
        if self.strengths:
            parts.append("Strengths:")
            for s in self.strengths:
                parts.append(f"- {s}")
        parts.append(
            f"Recommended strategy: {self.recommended_strategy}"
            f" (confidence: {self.confidence:.2f})"
        )
        if self.disambiguation_applied:
            parts.append(
                f"Disambiguation: {self.disambiguation_from} \u2192 {self.task_type}"
            )
        if self.llm_fallback_applied:
            parts.append(
                f"LLM classification fallback: applied (from {self.disambiguation_from or 'ambiguous'})"
            )
        return "\n".join(parts)


_DEFAULT_STRATEGY_MAP: dict[str, str] = {
    "coding": "structured-output",
    "writing": "role-playing",
    "analysis": "chain-of-thought",
    "creative": "role-playing",
    "data": "structured-output",
    "system": "meta-prompting",
    "general": "auto",
}


def _enrich_domain_qualifier(domain: str, prompt_lower: str) -> str:
    """Enrich a plain domain label with a sub-qualifier from organic vocabulary.

    Local wrapper that binds ``get_signal_loader`` via *this* module's
    namespace — kept here so tests that patch
    ``app.services.heuristic_analyzer.get_signal_loader`` continue to
    influence the enrichment path.  The canonical implementation lives in
    :func:`app.services.domain_detector.enrich_domain_qualifier`; this
    wrapper duplicates the body so the loader lookup happens through the
    orchestrator's module globals (which the test patches target).
    """
    if ":" in domain:
        return domain

    primary = domain.strip().lower()

    try:
        loader = get_signal_loader()
        if not loader:
            return domain
        qualifiers = loader.get_qualifiers(primary)
    except Exception:
        return domain

    if not qualifiers:
        return domain

    from app.services.domain_signal_loader import DomainSignalLoader
    from app.services.taxonomy._constants import SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS

    best_qualifier, best_hits = DomainSignalLoader.find_best_qualifier(
        prompt_lower, qualifiers,
    )

    if best_qualifier and best_hits >= SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS:
        logger.debug(
            "qualifier_enrichment: domain=%s qualifier=%s hits=%d",
            primary, best_qualifier, best_hits,
        )
        return f"{primary}: {best_qualifier}"
    return domain


class HeuristicAnalyzer:
    """Prompt classifier and weakness detector.

    Primarily zero-LLM (keyword-based).  Falls back to a fast Haiku LLM
    call when heuristic confidence is ambiguous (A4 confidence-gated
    fallback, ~15-20% of prompts).  Orchestrates the three extracted
    sub-modules; the legacy ``HeuristicAnalyzer`` surface area is preserved
    for every external caller (tests, routers, MCP, pipeline services).
    """

    async def analyze(
        self, raw_prompt: str, db: AsyncSession,
        *,
        enable_llm_fallback: bool = True,
    ) -> HeuristicAnalysis:
        """Classify prompt and detect weaknesses.  May invoke LLM for ambiguous cases.

        Args:
            raw_prompt: The user's raw prompt text.
            db: Async database session.
            enable_llm_fallback: When False, skip A4 confidence-gated LLM
                fallback.  Controlled by ``enable_llm_classification_fallback``
                preference.
        """
        try:
            return await self._analyze_inner(
                raw_prompt, db, enable_llm_fallback=enable_llm_fallback,
            )
        except Exception:
            logger.exception("Heuristic analysis failed — returning general fallback")
            return HeuristicAnalysis(
                task_type="general", domain="general",
                intent_label="general optimization",
                confidence=0.0,
            )

    async def _analyze_inner(
        self, raw_prompt: str, db: AsyncSession,
        *, enable_llm_fallback: bool = True,
    ) -> HeuristicAnalysis:
        prompt_lower = raw_prompt.lower()
        words = prompt_lower.split()
        # E.2: split on any sentence terminator (. ? !), not just `.` —
        # prompts ending in `?` with no trailing period had first_sentence
        # == whole, so every keyword received the 2x first-sentence boost.
        first_sentence = re.split(r"[.?!]", prompt_lower, maxsplit=1)[0]

        # Resolve task-type signals through the accessor so warm-path refreshes
        # (set_task_type_signals) are picked up without re-import. Import-time
        # rebinding in task_type_classifier does not update stale module-level
        # refs here — this lookup reads the authoritative dict every call.
        signals = get_task_type_signals()

        # Layer 1: Keyword classification
        task_type, task_confidence, all_scores = classify_task_type(
            prompt_lower, first_sentence, signals,
        )

        # Track whether dynamic (live TF-IDF extraction this run) or bootstrap
        # (static defaults or cache-warmup) signals were used for classification.
        # A4: explicit extraction-state tracking — presence in the merged signal
        # table is insufficient because disk-cache-warmup at boot looks identical
        # to signals just extracted in the current run, but only the latter prove
        # live learning has fired.
        _signal_source = (
            "dynamic" if task_type_has_dynamic_signals(task_type) else "bootstrap"
        )

        # Layer 1b: Technical verb disambiguation (A2)
        disambiguation_applied = False
        disambiguation_from: str | None = None
        if task_type in ("creative", "general"):
            if check_technical_disambiguation(first_sentence):
                coding_score = score_category(
                    prompt_lower, first_sentence, signals["coding"],
                )
                if coding_score > 0:
                    disambiguation_from = task_type
                    task_type = "coding"
                    task_confidence = max(task_confidence, coding_score, 0.6)
                    disambiguation_applied = True
                    logger.info(
                        "heuristic_disambiguation: %s → coding, prompt=%.80s",
                        disambiguation_from, raw_prompt,
                    )

        # Layer 1c: Confidence-gated LLM fallback (A4)
        # When heuristic confidence is low AND top two categories are close,
        # defer to a fast Haiku call for classification.  Gated by
        # enable_llm_fallback preference (default True) to support zero-LLM
        # workflows.
        llm_fallback_applied = False
        if (
            enable_llm_fallback
            and not disambiguation_applied
            and task_confidence < _LLM_CLASSIFICATION_CONFIDENCE_GATE
        ):
            sorted_vals = sorted(all_scores.values(), reverse=True)
            margin = (sorted_vals[0] - sorted_vals[1]) if len(sorted_vals) >= 2 else 999
            if margin < _LLM_CLASSIFICATION_MARGIN_GATE:
                llm_result = await classify_with_llm(raw_prompt, db)
                if llm_result:
                    disambiguation_from = task_type
                    task_type = llm_result[0]
                    task_confidence = 0.8  # LLM classification is higher confidence than heuristic
                    llm_fallback_applied = True
                    logger.info(
                        "llm_classification_fallback: heuristic=%s → llm=%s "
                        "domain=%s margin=%.2f prompt=%.80s",
                        disambiguation_from, task_type, llm_result[1],
                        margin, raw_prompt,
                    )

        # Domain classification via DomainSignalLoader (dynamic signals)
        word_set = set(words)
        loader = get_signal_loader()
        domain_scores = loader.score(word_set) if loader is not None else {}
        domain = _classify_domain(domain_scores)
        # Enrich domain with sub-qualifier from prompt keywords
        domain = _enrich_domain_qualifier(domain, prompt_lower)
        domain_confidence = min(1.0, max(domain_scores.values())) if domain_scores else 0.0

        # Layer 2: Structural signals
        has_code = _has_code_blocks(raw_prompt)
        has_lists = _has_markdown_lists(raw_prompt)
        is_question = first_sentence.strip().startswith(
            ("what", "how", "why", "when", "which", "is", "are", "can", "does"),
        )

        # Boost coding confidence if code blocks present
        if has_code and task_type != "coding":
            coding_score = score_category(
                prompt_lower, first_sentence, signals.get("coding", []),
            )
            if coding_score > task_confidence * 0.7:
                task_type = "coding"
                task_confidence = max(task_confidence, coding_score)

        # Boost analysis confidence if question form detected
        if is_question and task_type not in ("coding", "analysis"):
            analysis_score = score_category(
                prompt_lower, first_sentence, signals.get("analysis", []),
            )
            if analysis_score > task_confidence * 0.5:
                task_type = "analysis"
                task_confidence = max(task_confidence, analysis_score)

        # Pre-compute shared keyword flags for weakness/strength detection
        from app.services.weakness_detector import (
            _AUDIENCE_KEYWORDS,
            _CONSTRAINT_KEYWORDS,
            _OUTCOME_KEYWORDS,
        )
        has_constraints = any(kw in prompt_lower for kw in _CONSTRAINT_KEYWORDS)
        has_outcome = any(kw in prompt_lower for kw in _OUTCOME_KEYWORDS)
        has_audience = any(kw in prompt_lower for kw in _AUDIENCE_KEYWORDS)

        # Layer 3: Weakness + strength detection
        weaknesses = detect_weaknesses(
            raw_prompt, prompt_lower, words, task_type,
            has_constraints=has_constraints,
            has_outcome=has_outcome,
            has_audience=has_audience,
        )
        strengths = detect_strengths(
            raw_prompt, prompt_lower, words,
            has_code_blocks=has_code,
            has_lists=has_lists,
            has_constraints=has_constraints,
            has_outcome=has_outcome,
        )

        # Layer 4: Strategy from adaptation tracker
        strategy = await self._select_strategy(db, task_type)

        # Layer 5: Intent label
        intent_label = self._generate_intent_label(raw_prompt, task_type, domain)

        # Combine confidence
        confidence = min(1.0, (task_confidence + domain_confidence) / 2)
        if task_type == "general":
            confidence = min(confidence, 0.3)

        return HeuristicAnalysis(
            task_type=task_type,
            domain=domain,
            intent_label=intent_label,
            weaknesses=weaknesses,
            strengths=strengths,
            recommended_strategy=strategy,
            confidence=round(confidence, 2),
            disambiguation_applied=disambiguation_applied,
            disambiguation_from=disambiguation_from,
            domain_scores={k: round(v, 2) for k, v in domain_scores.items()} if domain_scores else None,
            llm_fallback_applied=llm_fallback_applied,
            task_type_signal_source=_signal_source,
            task_type_scores={k: round(v, 2) for k, v in all_scores.items()} if all_scores else None,
        )

    # ------------------------------------------------------------------
    # Legacy test hooks — preserved as instance methods because test files
    # exercise them via ``HeuristicAnalyzer()._classify(...)`` etc.
    # These are thin pass-throughs to the extracted modules.
    # ------------------------------------------------------------------

    @staticmethod
    def _classify(
        prompt_lower: str, first_sentence: str,
        signals: dict[str, list[tuple[str, float]]],
    ) -> tuple[str, float, dict[str, float]]:
        return classify_task_type(prompt_lower, first_sentence, signals)

    @staticmethod
    def _score_category(
        prompt_lower: str, first_sentence: str,
        keywords: list[tuple[str, float]],
    ) -> float:
        return score_category(prompt_lower, first_sentence, keywords)

    @staticmethod
    def _check_technical_disambiguation(first_sentence: str) -> bool:
        return check_technical_disambiguation(first_sentence)

    @staticmethod
    async def _classify_with_llm(
        raw_prompt: str,
        db: AsyncSession,
    ) -> tuple[str, str] | None:
        return await classify_with_llm(raw_prompt, db)

    @staticmethod
    def _detect_weaknesses(
        raw_prompt: str, prompt_lower: str,
        words: list[str], task_type: str,
        has_constraints: bool, has_outcome: bool,
        has_audience: bool,
    ) -> list[str]:
        return detect_weaknesses(
            raw_prompt, prompt_lower, words, task_type,
            has_constraints=has_constraints,
            has_outcome=has_outcome,
            has_audience=has_audience,
        )

    @staticmethod
    def _detect_strengths(
        raw_prompt: str, prompt_lower: str,
        words: list[str], has_code_blocks: bool, has_lists: bool,
        has_constraints: bool, has_outcome: bool,
    ) -> list[str]:
        return detect_strengths(
            raw_prompt, prompt_lower, words,
            has_code_blocks=has_code_blocks,
            has_lists=has_lists,
            has_constraints=has_constraints,
            has_outcome=has_outcome,
        )

    # ------------------------------------------------------------------
    # Strategy recommender
    # ------------------------------------------------------------------

    async def _select_strategy(
        self, db: AsyncSession, task_type: str,
    ) -> str:
        """Select strategy: historical learning → adaptation → static fallback."""
        learned = await self._learn_from_history(db, task_type)
        if learned:
            return learned

        try:
            from app.services.adaptation_tracker import AdaptationTracker
            tracker = AdaptationTracker(db)
            affinities = await tracker.get_affinities(task_type)
            blocked = await tracker.get_blocked_strategies(task_type)
            if affinities:
                candidates = {
                    k: v for k, v in affinities.items()
                    if k not in blocked and v.get("approval_rate", 0) > 0.6
                }
                if candidates:
                    best_key = max(candidates, key=lambda k: candidates[k].get("approval_rate", 0))
                    return best_key
        except Exception:
            logger.debug("Adaptation tracker unavailable", exc_info=True)

        return _DEFAULT_STRATEGY_MAP.get(task_type, "auto")

    async def _learn_from_history(
        self, db: AsyncSession, task_type: str,
    ) -> str | None:
        """Query historical strategy performance for this task_type.

        Includes passthrough results that have at least one thumbs_up feedback,
        since user validation confirms quality regardless of the scoring source.
        Unvalidated passthrough and heuristic-only results are excluded.
        """
        try:
            from app.models import Feedback, Optimization

            has_positive_feedback = exists(
                select(Feedback.id).where(
                    Feedback.optimization_id == Optimization.id,
                    Feedback.rating == "thumbs_up",
                ).correlate(Optimization)
            )

            result = await db.execute(
                select(
                    Optimization.strategy_used,
                    func.avg(Optimization.overall_score).label("avg_score"),
                    func.count().label("count"),
                )
                .where(
                    Optimization.task_type == task_type,
                    Optimization.status == "completed",
                    Optimization.overall_score.isnot(None),
                    or_(
                        Optimization.scoring_mode.notin_(["heuristic", "hybrid_passthrough"]),
                        has_positive_feedback,
                    ),
                )
                .group_by(Optimization.strategy_used)
                .having(func.count() >= 3)
            )
            rows = result.all()
            if not rows:
                return None
            best = max(rows, key=lambda r: r.avg_score)
            if best.avg_score >= 6.0:
                return best.strategy_used
        except Exception:
            logger.debug("Historical learning query failed", exc_info=True)
        return None

    # ------------------------------------------------------------------
    # Intent label generation
    # ------------------------------------------------------------------

    def _generate_intent_label(
        self, raw_prompt: str, task_type: str, domain: str,
    ) -> str:
        """Generate a short 3-6 word intent label.

        Priority chain:
        1. Verb + noun phrase from prompt (most specific).
        2. Verb + domain + task_type template (generic but categorized).
        3. task_type fallback (last resort).
        """
        from app.utils.text_cleanup import title_case_label

        first_verb = self._extract_first_verb(raw_prompt)
        if first_verb is not None:
            noun_phrase = self._extract_noun_phrase(raw_prompt, first_verb)
            if noun_phrase:
                label = f"{first_verb} {noun_phrase}"
            elif domain != "general":
                label = f"{first_verb} {domain} {task_type} task"
            else:
                label = f"{first_verb} {task_type} task"
        else:
            meaningful = self._extract_meaningful_words(raw_prompt, max_words=4)
            if meaningful:
                label = f"{task_type} {meaningful}"
            else:
                label = f"{task_type} optimization"

        words = label.split()[:6]
        return title_case_label(" ".join(words))

    @staticmethod
    def _extract_first_verb(text: str) -> str | None:
        """Extract the first likely verb from the prompt, or None if not found."""
        words = text.lower().split()
        for word in words[:10]:
            cleaned = re.sub(r"[^a-z]", "", word)
            if cleaned in _COMMON_VERBS:
                return cleaned
        return None

    @staticmethod
    def _extract_noun_phrase(text: str, verb: str) -> str | None:
        """Extract up to 3 meaningful words after the verb from the prompt.

        Skips articles, prepositions, and other stop words to capture the
        semantic core of what follows the verb.  Returns None if fewer
        than 1 meaningful word found after the verb.
        """
        words = text.lower().split()
        verb_idx = -1
        for i, w in enumerate(words[:10]):
            cleaned = re.sub(r"[^a-z]", "", w)
            if cleaned == verb:
                verb_idx = i
                break
        if verb_idx < 0:
            return None

        meaningful: list[str] = []
        for w in words[verb_idx + 1 : verb_idx + 12]:  # scan up to 11 words ahead
            cleaned = re.sub(r"[^a-z0-9]", "", w.lower())
            if not cleaned or cleaned in LABEL_STOP_WORDS:
                continue
            meaningful.append(cleaned)
            if len(meaningful) >= 3:
                break

        return " ".join(meaningful) if meaningful else None

    @staticmethod
    def _extract_meaningful_words(text: str, max_words: int = 4) -> str | None:
        """Extract the first N meaningful words from text, skipping stop words.

        Delegates to the canonical ``extract_meaningful_words()`` from
        ``text_cleanup`` with verb exclusion for label disambiguation.
        """
        return extract_meaningful_words(
            text, max_words=max_words, scan_window=20, exclude=_COMMON_VERBS,
        )


# ---------------------------------------------------------------------------
# Public re-exports — preserve every symbol that external callers import
# from this module.  Splits are invisible to the rest of the codebase.
# ---------------------------------------------------------------------------

__all__ = [
    # Primary API
    "HeuristicAnalysis",
    "HeuristicAnalyzer",
    # Signal loader shims
    "get_signal_loader",
    "set_signal_loader",
    # Task-type signals refresh (warm path Phase 4.75)
    "set_task_type_signals",
    # Private but test-imported — preserved for backward compat
    "_enrich_domain_qualifier",
]
