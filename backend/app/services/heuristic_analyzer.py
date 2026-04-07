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

from app.utils.text_cleanup import LABEL_STOP_WORDS, extract_meaningful_words


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
        return "\n".join(parts)


# --- Weighted keyword signals (case-insensitive matching) ---

_TASK_TYPE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        ("implement", 1.0), ("refactor", 1.0), ("debug", 0.9),
        ("function", 0.7), ("api", 0.8), ("endpoint", 0.8),
        ("bug", 0.9), ("test", 0.7), ("deploy", 0.6),
        ("class", 0.6), ("module", 0.6), ("code", 0.5),
        ("fix", 0.6), ("build", 0.7), ("migrate", 0.7),
        ("database", 0.5), ("calculate", 0.6),
    ],
    "writing": [
        ("write", 0.6), ("draft", 0.9), ("blog", 1.0),
        ("article", 1.0), ("essay", 1.0), ("copy", 0.8),
        ("tone", 0.7), ("audience", 0.6), ("narrative", 0.8),
        ("publish", 0.7), ("editorial", 0.9),
    ],
    "analysis": [
        ("analyze", 1.0), ("compare", 0.9), ("evaluate", 0.9),
        ("review", 0.7), ("assess", 0.9), ("critique", 0.8),
        ("pros and cons", 0.9), ("trade-off", 0.8), ("tradeoff", 0.8),
        ("investigate", 0.7), ("examine", 0.7),
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

# Pre-compiled word-boundary patterns for task_type keywords.
# Built once at import time to avoid recompilation in hot loops.
# Domain patterns are managed by DomainSignalLoader._precompile_patterns().
_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {}


def _precompile_keyword_patterns() -> None:
    """Pre-compile regex for all single-word task_type signals at module load."""
    for keywords in _TASK_TYPE_SIGNALS.values():
        for keyword, _weight in keywords:
            kw = keyword.lower()
            if " " not in kw and kw not in _KEYWORD_PATTERNS:
                _KEYWORD_PATTERNS[kw] = re.compile(
                    r"\b" + re.escape(kw) + r"\b",
                )


_precompile_keyword_patterns()


def _classify_domain(scored: dict[str, float]) -> str:
    """Classify domain by delegating to the DomainSignalLoader.

    Returns ``"general"`` when no signal loader is configured (e.g. during
    early startup or in tests that don't seed domain nodes).
    """
    loader = _get_signal_loader()
    if loader is None:
        return "general"
    return loader.classify(scored)


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
    """Zero-LLM prompt classifier and weakness detector."""

    async def analyze(
        self, raw_prompt: str, db: AsyncSession,
    ) -> HeuristicAnalysis:
        """Classify prompt and detect weaknesses without any LLM calls."""
        try:
            return await self._analyze_inner(raw_prompt, db)
        except Exception:
            logger.exception("Heuristic analysis failed — returning general fallback")
            return HeuristicAnalysis(
                task_type="general", domain="general",
                intent_label="general optimization",
                confidence=0.0,
            )

    async def _analyze_inner(
        self, raw_prompt: str, db: AsyncSession,
    ) -> HeuristicAnalysis:
        prompt_lower = raw_prompt.lower()
        words = prompt_lower.split()
        first_sentence = prompt_lower.split(".")[0] if "." in prompt_lower else prompt_lower

        # Layer 1: Keyword classification
        task_type, task_confidence = self._classify(
            prompt_lower, first_sentence, _TASK_TYPE_SIGNALS,
        )
        # Domain classification via DomainSignalLoader (dynamic signals)
        word_set = set(words)
        loader = _get_signal_loader()
        if loader is not None:
            domain_scores = loader.score(word_set)
        else:
            domain_scores = {}
        domain = _classify_domain(domain_scores)
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
        )

    def _classify(
        self, prompt_lower: str, first_sentence: str,
        signals: dict[str, list[tuple[str, float]]],
    ) -> tuple[str, float]:
        """Score all categories and return (best_category, confidence)."""
        scores: dict[str, float] = {}
        for category, keywords in signals.items():
            scores[category] = self._score_category(
                prompt_lower, first_sentence, keywords,
            )
        if not scores or max(scores.values()) == 0:
            return "general", 0.0
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return best, min(1.0, scores[best])

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
