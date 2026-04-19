"""Zero-LLM heuristic prompt analyzer.

Classifies task_type, domain, detects weaknesses/strengths, and recommends
a strategy — all without any LLM calls. Designed for passthrough tier
enrichment where we cannot call external models.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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


def _get_signal_loader():
    """Resolve the signal loader from the service-level singleton.

    Returns None if not yet initialized (startup race, tests without seeding).
    """
    from app.services.domain_signal_loader import get_signal_loader
    return get_signal_loader()


# Legacy aliases — main.py and mcp_server.py call set_signal_loader().
# Delegates to the service module singleton so there's one source of truth.
def set_signal_loader(loader) -> None:
    """Set the DomainSignalLoader singleton (called from lifespan)."""
    from app.services.domain_signal_loader import set_signal_loader as _set
    _set(loader)


def get_signal_loader():
    """Return the DomainSignalLoader singleton."""
    return _get_signal_loader()


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
    # Task-type signal source tracking
    task_type_signal_source: str = "static"  # "dynamic" | "static"
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


# --- Weighted keyword signals (case-insensitive matching) ---

_TASK_TYPE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        # Compound signals (high weight — override single-word collisions)
        # Compound signals (high weight — override single-word collisions like "design" → creative)
        ("design a system", 1.3), ("design a service", 1.3),
        ("design a schema", 1.2), ("design an api", 1.3),
        ("design a database", 1.2), ("design a pipeline", 1.2),
        ("design and implement", 1.3), ("design a middleware", 1.2),
        ("create a migration", 1.1), ("create an endpoint", 1.1),
        ("create a service", 1.1), ("create a middleware", 1.1),
        ("build a service", 1.2), ("build a system", 1.2),
        ("build an api", 1.2), ("build a queue", 1.1),
        ("add a feature", 1.0), ("add an endpoint", 1.1),
        ("delivery system", 1.1), ("retry logic", 1.0),
        ("dead letter", 1.0), ("rate limiter", 1.0),
        # Single-word signals
        ("implement", 1.0), ("refactor", 1.0), ("debug", 0.9),
        ("function", 0.7), ("api", 0.8), ("endpoint", 0.8),
        ("bug", 0.9), ("test", 0.7), ("deploy", 0.6),
        ("class", 0.6), ("module", 0.6), ("code", 0.5),
        ("fix", 0.6), ("build", 0.7), ("migrate", 0.7),
        ("database", 0.5), ("calculate", 0.6),
        ("backend", 0.6), ("frontend", 0.5), ("middleware", 0.7),
        ("websocket", 0.7), ("server", 0.5), ("schema", 0.6),
        ("kubernetes", 0.6), ("docker", 0.5), ("ci/cd", 0.7),
        ("helm", 0.6), ("graphql", 0.7), ("microservice", 0.7),
    ],
    "writing": [
        # Compound signals
        ("design a campaign", 1.1), ("create content", 1.0),
        ("write a blog", 1.2), ("write an article", 1.2),
        ("write a guide", 1.1),
        # Single-word signals
        ("write", 0.6), ("draft", 0.9), ("blog", 1.0),
        ("article", 1.0), ("essay", 1.0), ("copy", 0.8),
        ("tone", 0.7), ("audience", 0.6), ("narrative", 0.8),
        ("publish", 0.7), ("editorial", 0.9),
    ],
    "analysis": [
        # Compound signals
        ("generate a report", 1.1), ("sales report", 1.0),
        ("quarterly report", 1.0), ("build a dashboard", 1.0),
        ("analyze the data", 1.1), ("evaluate the performance", 1.0),
        ("year-over-year", 0.9), ("compare revenue", 0.9),
        # Single-word signals
        ("analyze", 1.0), ("compare", 0.9), ("evaluate", 0.9),
        ("review", 0.7), ("assess", 0.9), ("critique", 0.8),
        ("pros and cons", 0.9), ("trade-off", 0.8), ("tradeoff", 0.8),
        ("investigate", 0.7), ("examine", 0.7),
        # Inspection verbs (E.1): audit/diagnose/inspect are common analysis
        # signals missing from the original set — prompts like "Audit X"
        # previously scored 0 on analysis and drifted to data/general.
        ("audit", 0.9), ("diagnose", 0.9), ("inspect", 0.8),
    ],
    "creative": [
        ("create", 0.5), ("brainstorm", 1.0), ("imagine", 0.9),
        ("story", 1.0), ("generate ideas", 0.9), ("creative", 0.8),
        ("invent", 0.9), ("design", 0.7), ("concept", 0.6),
    ],
    "data": [
        ("data", 0.6), ("dataset", 0.9), ("etl", 1.0),
        ("pipeline", 0.6), ("transform", 0.6), ("schema", 0.7),
        ("query", 0.7), ("aggregate", 0.8), ("visualization", 0.7),
        ("csv", 0.8), ("dataframe", 0.9), ("pandas", 0.9),
    ],
    "system": [
        ("system prompt", 1.0), ("agent", 0.7), ("workflow", 0.6),
        ("automate", 0.8), ("orchestrate", 0.9), ("configure", 0.7),
        ("setup", 0.5), ("infrastructure", 0.7), ("prompt engineer", 0.9),
    ],
}

# Static compound signals: extracted once at module load, preserved on every dynamic update.
# These solve structural language patterns ("design a system" = coding, not creative)
# that TF-IDF cannot discover from single-word tokenization.
_STATIC_COMPOUND_SIGNALS: dict[str, list[tuple[str, float]]] = {
    task_type: [(kw, w) for kw, w in keywords if " " in kw]
    for task_type, keywords in _TASK_TYPE_SIGNALS.items()
}

# --- A4: Confidence-gated LLM fallback thresholds ---
_LLM_CLASSIFICATION_CONFIDENCE_GATE = 0.5  # heuristic confidence below this triggers check
_LLM_CLASSIFICATION_MARGIN_GATE = 0.2      # margin between top 2 categories below this triggers LLM

# --- Technical verb + noun disambiguation ---
# When a technical verb appears with a technical noun in the first sentence,
# the prompt is almost certainly coding-related, even if "design" or "create"
# triggered the creative category. Checked post-classification.
_TECHNICAL_VERBS = frozenset({
    "design", "create", "build", "set", "configure", "add", "implement",
    "refactor", "debug", "migrate", "deploy", "test", "develop",
})
_TECHNICAL_NOUNS = frozenset({
    "system", "service", "api", "endpoint", "schema", "database",
    "middleware", "pipeline", "queue", "cache", "scheduler", "server",
    "backend", "frontend", "module", "library", "framework", "migration",
    "table", "index", "model", "route", "handler", "worker",
})

# Pre-compiled word-boundary patterns for task_type keywords.
# Built once at import time to avoid recompilation in hot loops.
# Domain patterns are managed by DomainSignalLoader._precompile_patterns().
_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {}


def _precompile_keyword_patterns() -> None:
    """Pre-compile regex for all single-word task_type signals at module load."""
    _KEYWORD_PATTERNS.clear()
    for keywords in _TASK_TYPE_SIGNALS.values():
        for keyword, _weight in keywords:
            kw = keyword.lower()
            if " " not in kw and kw not in _KEYWORD_PATTERNS:
                _KEYWORD_PATTERNS[kw] = re.compile(
                    r"\b" + re.escape(kw) + r"\b",
                )


_precompile_keyword_patterns()


def set_task_type_signals(dynamic_signals: dict[str, list[tuple[str, float]]]) -> None:
    """Merge dynamic single-word signals with static compound signals.

    Called by warm-path Phase 4.75 and backend/MCP lifespan.
    Validates input, merges, clears + rebuilds pattern cache.
    """
    if not dynamic_signals:
        logger.warning("set_task_type_signals: empty dict — keeping current signals")
        return
    for task_type, keywords in dynamic_signals.items():
        if not isinstance(keywords, list):
            logger.warning("set_task_type_signals: invalid keywords for %s — aborting", task_type)
            return
    merged: dict[str, list[tuple[str, float]]] = {}
    for task_type in set(list(dynamic_signals.keys()) + list(_STATIC_COMPOUND_SIGNALS.keys())):
        compounds = _STATIC_COMPOUND_SIGNALS.get(task_type, [])
        singles = dynamic_signals.get(task_type, [])
        merged[task_type] = compounds + singles
    global _TASK_TYPE_SIGNALS
    _TASK_TYPE_SIGNALS = merged
    _precompile_keyword_patterns()
    logger.info(
        "TaskTypeSignals: merged %d task types, %d total keywords (%d compound + %d dynamic)",
        len(merged), sum(len(v) for v in merged.values()),
        sum(len(v) for v in _STATIC_COMPOUND_SIGNALS.values()),
        sum(len(v) for v in dynamic_signals.values()),
    )


def _classify_domain(scored: dict[str, float]) -> str:
    """Classify domain by delegating to the DomainSignalLoader.

    Returns ``"general"`` when no signal loader is configured (e.g. during
    early startup or in tests that don't seed domain nodes).
    """
    loader = _get_signal_loader()
    if loader is None:
        return "general"
    return loader.classify(scored)


def _enrich_domain_qualifier(domain: str, prompt_lower: str) -> str:
    """Enrich a plain domain label with a sub-qualifier from organic vocabulary.

    Reads qualifier vocabulary from ``DomainSignalLoader.get_qualifiers()``,
    which is populated organically by Haiku from cluster labels during the
    warm path's Phase 5 discovery.

    If *domain* already contains a qualifier (has ``:``) or the loader has
    no vocabulary for this domain, returns the original string unchanged.

    Returns:
        Enriched domain string (e.g., ``"saas: growth"``) or original.
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

    best_qualifier, best_hits = DomainSignalLoader.find_best_qualifier(
        prompt_lower, qualifiers,
    )

    from app.services.taxonomy._constants import SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS

    if best_qualifier and best_hits >= SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS:
        logger.debug(
            "qualifier_enrichment: domain=%s qualifier=%s hits=%d",
            primary, best_qualifier, best_hits,
        )
        return f"{primary}: {best_qualifier}"
    return domain


_DEFAULT_STRATEGY_MAP: dict[str, str] = {
    "coding": "structured-output",
    "writing": "role-playing",
    "analysis": "chain-of-thought",
    "creative": "role-playing",
    "data": "structured-output",
    "system": "meta-prompting",
    "general": "auto",
}

# Vague quantifier patterns
_VAGUE_PATTERNS = re.compile(
    r"\b(some|various|many|a few|several|certain|stuff|things|better|improve)\b",
    re.IGNORECASE,
)

# Code block detection
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

# Constraint/requirement keywords
_CONSTRAINT_KEYWORDS = {
    "must", "should", "require", "constraint", "limit", "maximum",
    "minimum", "exactly", "no more than", "at least", "ensure",
}

# Success criteria keywords
_OUTCOME_KEYWORDS = {
    "return", "output", "produce", "result", "generate", "create",
    "should return", "expected", "format",
}

# Audience/persona keywords
_AUDIENCE_KEYWORDS = {
    "audience", "persona", "reader", "user", "customer", "developer",
    "beginner", "expert", "stakeholder", "team", "client",
}


class HeuristicAnalyzer:
    """Prompt classifier and weakness detector.

    Primarily zero-LLM (keyword-based). Falls back to a fast Haiku LLM call
    when heuristic confidence is ambiguous (A4 confidence-gated fallback).
    """

    async def analyze(
        self, raw_prompt: str, db: AsyncSession,
        *,
        enable_llm_fallback: bool = True,
    ) -> HeuristicAnalysis:
        """Classify prompt and detect weaknesses. May invoke LLM for ambiguous cases.

        Args:
            raw_prompt: The user's raw prompt text.
            db: Async database session.
            enable_llm_fallback: When False, skip A4 confidence-gated LLM fallback.
                Controlled by ``enable_llm_classification_fallback`` preference.
        """
        try:
            return await self._analyze_inner(raw_prompt, db, enable_llm_fallback=enable_llm_fallback)
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
        # E.2: split on any sentence terminator (. ? !), not just `.` — otherwise
        # prompts ending in `?` with no trailing period had first_sentence == whole,
        # so every keyword received the 2x first-sentence boost.
        first_sentence = re.split(r"[.?!]", prompt_lower, maxsplit=1)[0]

        # Layer 1: Keyword classification
        task_type, task_confidence, all_scores = self._classify(
            prompt_lower, first_sentence, _TASK_TYPE_SIGNALS,
        )

        # Track whether dynamic or static signals were used for classification
        _has_dynamic_singles = bool(_TASK_TYPE_SIGNALS.get(task_type)) and any(
            " " not in kw for kw, _ in _TASK_TYPE_SIGNALS.get(task_type, [])
            if (kw, _) not in _STATIC_COMPOUND_SIGNALS.get(task_type, [])
        )
        _signal_source = "dynamic" if _has_dynamic_singles else "static"

        # Layer 1b: Technical verb disambiguation (A2)
        disambiguation_applied = False
        disambiguation_from: str | None = None
        if task_type in ("creative", "general"):
            if self._check_technical_disambiguation(first_sentence):
                coding_score = self._score_category(
                    prompt_lower, first_sentence, _TASK_TYPE_SIGNALS["coding"],
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
        # defer to a fast Haiku call for classification.
        # Gated by enable_llm_fallback preference (default True) to support zero-LLM workflows.
        llm_fallback_applied = False
        if enable_llm_fallback and not disambiguation_applied and task_confidence < _LLM_CLASSIFICATION_CONFIDENCE_GATE:
            sorted_vals = sorted(all_scores.values(), reverse=True)
            margin = (sorted_vals[0] - sorted_vals[1]) if len(sorted_vals) >= 2 else 999
            if margin < _LLM_CLASSIFICATION_MARGIN_GATE:
                llm_result = await self._classify_with_llm(raw_prompt, db)
                if llm_result:
                    disambiguation_from = task_type
                    task_type = llm_result[0]
                    task_confidence = 0.8  # LLM classification is higher confidence than heuristic
                    llm_fallback_applied = True
                    logger.info(
                        "llm_classification_fallback: heuristic=%s → llm=%s domain=%s margin=%.2f prompt=%.80s",
                        disambiguation_from, task_type, llm_result[1], margin, raw_prompt,
                    )

        # Domain classification via DomainSignalLoader (dynamic signals)
        word_set = set(words)
        loader = _get_signal_loader()
        if loader is not None:
            domain_scores = loader.score(word_set)
        else:
            domain_scores = {}
        domain = _classify_domain(domain_scores)
        # Enrich domain with sub-qualifier from prompt keywords
        domain = _enrich_domain_qualifier(domain, prompt_lower)
        domain_confidence = min(1.0, max(domain_scores.values())) if domain_scores else 0.0

        # Layer 2: Structural signals
        has_code_blocks = bool(_CODE_BLOCK_RE.search(raw_prompt))
        has_lists = bool(re.search(r"^\s*[-*]\s", raw_prompt, re.MULTILINE))
        is_question = first_sentence.strip().startswith(
            ("what", "how", "why", "when", "which", "is", "are", "can", "does"),
        )

        # Boost coding confidence if code blocks present
        if has_code_blocks and task_type != "coding":
            coding_score = self._score_category(prompt_lower, first_sentence, _TASK_TYPE_SIGNALS.get("coding", []))
            if coding_score > task_confidence * 0.7:
                task_type = "coding"
                task_confidence = max(task_confidence, coding_score)

        # Boost analysis confidence if question form detected
        if is_question and task_type not in ("coding", "analysis"):
            analysis_score = self._score_category(prompt_lower, first_sentence, _TASK_TYPE_SIGNALS.get("analysis", []))
            if analysis_score > task_confidence * 0.5:
                task_type = "analysis"
                task_confidence = max(task_confidence, analysis_score)

        # Pre-compute shared keyword flags for weakness/strength detection
        has_constraints = any(kw in prompt_lower for kw in _CONSTRAINT_KEYWORDS)
        has_outcome = any(kw in prompt_lower for kw in _OUTCOME_KEYWORDS)
        has_audience = any(kw in prompt_lower for kw in _AUDIENCE_KEYWORDS)

        # Layer 3: Weakness detection
        weaknesses = self._detect_weaknesses(
            raw_prompt, prompt_lower, words, task_type,
            has_constraints, has_outcome, has_audience,
        )
        strengths = self._detect_strengths(
            raw_prompt, prompt_lower, words, has_code_blocks, has_lists,
            has_constraints, has_outcome,
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

    @staticmethod
    def _check_technical_disambiguation(first_sentence: str) -> bool:
        """Check if the first sentence contains a technical verb + noun pair.

        Scans for any verb from _TECHNICAL_VERBS followed within 4 words by a
        noun from _TECHNICAL_NOUNS. Handles articles/prepositions in between
        (e.g., "design a REST api", "build the caching system").
        Words are stripped of trailing punctuation before matching.
        """
        # Strip punctuation from each word so "system." matches "system"
        words = [w.strip(".,;:!?()[]{}\"'") for w in first_sentence.split()]
        for i, word in enumerate(words):
            if word in _TECHNICAL_VERBS:
                # Check next 4 words for a technical noun
                for j in range(i + 1, min(i + 5, len(words))):
                    if words[j] in _TECHNICAL_NOUNS:
                        return True
        return False

    @staticmethod
    async def _classify_with_llm(
        raw_prompt: str,
        db: AsyncSession,
    ) -> tuple[str, str] | None:
        """Fast LLM classification fallback using Haiku.

        Returns (task_type, domain) or None on failure.
        Only called when heuristic confidence is ambiguous (A4 gate).
        Minimal prompt — ~500 input tokens, ~20 output tokens.
        """
        try:
            from pydantic import BaseModel as _BaseModel

            from app.config import settings
            from app.providers.base import call_provider_with_retry
            from app.providers.detector import detect_provider

            provider = detect_provider()
            if provider is None:
                logger.debug("llm_classification_fallback: no provider available")
                return None

            # Build known domains list
            known_domains = ["backend", "frontend", "database", "devops", "security", "general"]
            try:
                from app.services.domain_signal_loader import get_signal_loader
                loader = get_signal_loader()
                if loader and loader.signals:
                    known_domains = list(loader.signals.keys()) + ["general"]
            except Exception:
                pass

            prompt_text = (
                "Classify this prompt into exactly one task type and one domain.\n\n"
                "Task types: coding, writing, analysis, creative, data, system, general\n"
                f"Domains: {', '.join(known_domains)}\n\n"
                f"Prompt: {raw_prompt[:500]}\n\n"
                "Return the classification."
            )

            class _ClassificationResult(_BaseModel):
                task_type: str
                domain: str

            # Wrap in call_provider_with_retry for parity with every other
            # Haiku call site. Transient rate-limit / overload errors retry
            # once; non-retryable errors (auth, bad request) and final-attempt
            # failures propagate out to the outer try/except and we degrade
            # gracefully to the heuristic result (returning None).
            result = await call_provider_with_retry(
                provider,
                model=getattr(settings, "MODEL_HAIKU", "claude-haiku-4-5"),
                system_prompt="You are a prompt classifier.",
                user_message=prompt_text,
                output_format=_ClassificationResult,
                max_tokens=100,
            )

            task_type = result.task_type
            domain = result.domain

            # Validate task_type
            valid_types = {"coding", "writing", "analysis", "creative", "data", "system", "general"}
            if task_type not in valid_types:
                task_type = "general"

            logger.info(
                "llm_classification_result: task_type=%s domain=%s",
                task_type, domain,
            )
            return task_type, domain

        except Exception:
            logger.debug("llm_classification_fallback failed", exc_info=True)
            return None

    def _classify(
        self, prompt_lower: str, first_sentence: str,
        signals: dict[str, list[tuple[str, float]]],
    ) -> tuple[str, float, dict[str, float]]:
        """Score all categories and return (best_category, confidence, all_scores)."""
        scores: dict[str, float] = {}
        for category, keywords in signals.items():
            scores[category] = self._score_category(
                prompt_lower, first_sentence, keywords,
            )
        if not scores or max(scores.values()) == 0:
            return "general", 0.0, scores
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return best, min(1.0, scores[best]), scores

    @staticmethod
    def _score_category(
        prompt_lower: str, first_sentence: str,
        keywords: list[tuple[str, float]],
    ) -> float:
        """Score a category by weighted keyword presence with positional boost.

        Uses pre-compiled word-boundary patterns to avoid false positives
        (e.g. "class" should not match "classification").  Multi-word
        keywords (e.g. "system prompt") use simple substring search since
        ``\\b`` would not match internal spaces correctly.
        """
        score = 0.0
        for keyword, weight in keywords:
            kw = keyword.lower()
            # Multi-word keywords: substring match (word-boundary would fail)
            if " " in kw:
                found_in_prompt = kw in prompt_lower
                found_in_first = kw in first_sentence
            else:
                pat = _KEYWORD_PATTERNS.get(kw)
                if pat:
                    found_in_prompt = bool(pat.search(prompt_lower))
                    found_in_first = bool(pat.search(first_sentence))
                else:
                    found_in_prompt = kw in prompt_lower
                    found_in_first = kw in first_sentence
            if found_in_prompt:
                # 2x boost if keyword appears in first sentence
                multiplier = 2.0 if found_in_first else 1.0
                score += weight * multiplier
        return score

    def _detect_weaknesses(
        self, raw_prompt: str, prompt_lower: str,
        words: list[str], task_type: str,
        has_constraints: bool, has_outcome: bool,
        has_audience: bool,
    ) -> list[str]:
        weaknesses: list[str] = []
        word_count = len(words)

        # Vague language
        vague_matches = _VAGUE_PATTERNS.findall(prompt_lower)
        if len(vague_matches) >= 2:
            weaknesses.append("vague language reduces precision")

        # Missing constraints
        if not has_constraints and word_count > 10:
            weaknesses.append("lacks constraints — no boundaries for the output")

        # Missing outcome
        if not has_outcome and word_count > 15:
            weaknesses.append("no measurable outcome defined")

        # Missing audience/persona
        if not has_audience and task_type in ("writing", "creative") and word_count > 10:
            weaknesses.append("target audience unclear")

        # Too short for complex task (spec: < 50 words for non-trivial)
        if task_type in ("coding", "data", "system") and word_count < 50:
            weaknesses.append("prompt underspecified for task complexity")

        # No examples
        has_examples = "example" in prompt_lower or "e.g." in prompt_lower or "```" in raw_prompt
        if not has_examples and word_count > 20:
            weaknesses.append("no examples to anchor expected output")

        # Broad scope
        if any(w in prompt_lower for w in ("everything", "all aspects", "every part")):
            weaknesses.append("scope too broad — consider narrowing focus")

        # Missing technical context for coding
        if task_type == "coding":
            tech_terms = {"python", "javascript", "typescript", "rust", "go", "java",
                          "react", "svelte", "fastapi", "django", "flask", "sql"}
            if not any(t in prompt_lower for t in tech_terms):
                weaknesses.append("insufficient technical context — no language or framework specified")

        return weaknesses

    def _detect_strengths(
        self, raw_prompt: str, prompt_lower: str,
        words: list[str], has_code_blocks: bool, has_lists: bool,
        has_constraints: bool, has_outcome: bool,
    ) -> list[str]:
        strengths: list[str] = []

        if has_code_blocks:
            strengths.append("includes concrete code examples")
        if has_lists:
            strengths.append("well-organized prompt structure")

        if has_constraints:
            strengths.append("clear constraints defined")

        # Specific technologies mentioned
        tech_count = sum(1 for t in (
            "python", "javascript", "typescript", "react", "svelte",
            "fastapi", "django", "sql", "docker", "kubernetes",
        ) if t in prompt_lower)
        if tech_count >= 2:
            strengths.append("specific technical context provided")

        if has_outcome:
            strengths.append("measurable outcome specified")

        return strengths

    async def _select_strategy(
        self, db: AsyncSession, task_type: str,
    ) -> str:
        """Select strategy: historical learning → adaptation → static fallback."""
        # Try historical learning first
        learned = await self._learn_from_history(db, task_type)
        if learned:
            return learned

        # Try adaptation tracker (use get_affinities — no get_best_strategy method)
        try:
            from app.services.adaptation_tracker import AdaptationTracker
            tracker = AdaptationTracker(db)
            affinities = await tracker.get_affinities(task_type)
            blocked = await tracker.get_blocked_strategies(task_type)
            if affinities:
                # Pick strategy with highest approval rate, excluding blocked
                candidates = {
                    k: v for k, v in affinities.items()
                    if k not in blocked and v.get("approval_rate", 0) > 0.6
                }
                if candidates:
                    best_key = max(candidates, key=lambda k: candidates[k].get("approval_rate", 0))
                    return best_key
        except Exception:
            logger.debug("Adaptation tracker unavailable", exc_info=True)

        # Static fallback
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

            # Correlated subquery: optimization has ≥1 thumbs_up feedback
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
                        # Include non-passthrough results (internal, sampling, hybrid)
                        Optimization.scoring_mode.notin_(["heuristic", "hybrid_passthrough"]),
                        # Include passthrough results validated by user thumbs_up
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

    def _generate_intent_label(
        self, raw_prompt: str, task_type: str, domain: str,
    ) -> str:
        """Generate a short 3-6 word intent label.

        Priority chain:
        1. Verb + noun phrase from prompt (most specific)
        2. Verb + domain + task_type template (generic but categorized)
        3. task_type fallback (last resort)
        """
        from app.utils.text_cleanup import title_case_label

        first_verb = self._extract_first_verb(raw_prompt)
        if first_verb is not None:
            # Try to extract a meaningful noun phrase after the verb
            noun_phrase = self._extract_noun_phrase(raw_prompt, first_verb)
            if noun_phrase:
                label = f"{first_verb} {noun_phrase}"
            elif domain != "general":
                label = f"{first_verb} {domain} {task_type} task"
            else:
                label = f"{first_verb} {task_type} task"
        else:
            # No verb found — try extracting meaningful words from the prompt
            meaningful = self._extract_meaningful_words(raw_prompt, max_words=4)
            if meaningful:
                label = f"{task_type} {meaningful}"
            else:
                label = f"{task_type} optimization"

        # Cap at 6 words, title-case for display consistency
        words = label.split()[:6]
        return title_case_label(" ".join(words))

    @staticmethod
    def _extract_first_verb(text: str) -> str | None:
        """Extract the first likely verb from the prompt, or None if not found."""
        words = text.lower().split()
        for word in words[:10]:  # Check first 10 words
            cleaned = re.sub(r"[^a-z]", "", word)
            if cleaned in _COMMON_VERBS:
                return cleaned
        return None

    @staticmethod
    def _extract_noun_phrase(text: str, verb: str) -> str | None:
        """Extract up to 3 meaningful words after the verb from the prompt.

        Skips articles, prepositions, and other stop words to capture the
        semantic core of what follows the verb.

        Returns None if fewer than 1 meaningful word found after the verb.
        """
        words = text.lower().split()
        # Find verb position in first 10 words
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
