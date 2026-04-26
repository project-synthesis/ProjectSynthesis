"""Task-type classification for heuristic prompt analysis.

Owns the weighted-keyword signal table, A1 compound keywords, A2 technical
verb+noun disambiguation, and the A4 confidence-gated Haiku LLM fallback.
Single module-level singleton of ``_TASK_TYPE_SIGNALS`` so that warm-path
Phase 4.75 can swap in dynamic signals discovered from the taxonomy.

Extracted from ``heuristic_analyzer.py`` (Phase 3F).  Public API is
preserved via re-exports in ``heuristic_analyzer.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_TASK_TYPE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        # Compound signals (high weight — override single-word collisions
        # like "design" → creative)
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
        # B1: ORM / factory / DI / pool compounds. Live reference:
        # "Design a SQLAlchemy async session factory with per-request
        # dependency injection for FastAPI". Pre-B1 this prompt only hit
        # "design" (creative 0.7 × 2 first-sentence = 1.4) because none of
        # these compound idioms scored on the coding table.
        ("session factory", 1.2), ("dependency injection", 1.1),
        ("connection pool", 1.0), ("design a factory", 1.2),
        ("build a factory", 1.2), ("design a session", 1.1),
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
        # A8: CLI-family coding artifacts. The live "Fastapi Log Tail CLI"
        # prompt classified as creative because "design" was the only
        # matched keyword — "cli"/"daemon"/"binary" have to score on the
        # coding table for A2 disambiguation to flip the verdict. Weights
        # deliberately moderate so they don't steamroll genuine writing
        # tasks ("a binary decision", "a command-line in the play script").
        ("cli", 0.7), ("daemon", 0.7), ("binary", 0.5),
        # B1: framework names. Live reference: "Design a SQLAlchemy async
        # session factory ... for FastAPI". No creative-writing prompt
        # says "design a FastAPI" — these identifiers are unambiguous tech
        # context and must register on the coding table so A2
        # disambiguation can flip "design" away from creative.
        ("sqlalchemy", 0.7), ("fastapi", 0.7),
        ("django", 0.7), ("flask", 0.6),
        ("factory", 0.5), ("session", 0.4),
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
        # Inspection compound signals (E1 follow-up): disambiguate nested
        # meta-prompts like "write a prompt that audits the X" — the inner
        # "audits the" should pull the classification toward analysis, while
        # the "write a prompt" outer wrapper (below, under system) still wins
        # because its weight is higher.  "audit of" covers the noun form.
        ("audits the", 0.9), ("audit the", 0.9), ("audit of", 0.9),
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
        # Meta-prompt compound signals (E1 root fix): "Write a prompt that X"
        # is prompt-engineering work, not writing.  Weight above the
        # inspection compound "audits the" (0.9) so nested prompts classify
        # as system rather than analysis.  These override the single "write"
        # signal (0.6) on writing.
        ("write a prompt", 1.3), ("write a spec", 1.2),
        ("write instructions", 1.1), ("write a system prompt", 1.4),
        ("craft a prompt", 1.3), ("design a prompt", 1.3),
        ("prompt that", 1.0),
        # #12 (2026-04-24 A1+A2 audit): "design a system prompt" must win
        # over the coding compound "design a system" (both 1.3).  Longer
        # compound at weight 1.5 ensures system wins when the user literally
        # asks for a system-prompt design — the substring "design a system"
        # also matches but the longer phrase earns priority via the higher
        # weight.  Same pattern: "build/create a system prompt".
        ("design a system prompt", 1.5),
        ("build a system prompt", 1.5),
        ("create a system prompt", 1.5),
        # Single-word signals
        ("system prompt", 1.0), ("agent", 0.7), ("workflow", 0.6),
        ("automate", 0.8), ("orchestrate", 0.9), ("configure", 0.7),
        ("setup", 0.5), ("infrastructure", 0.7), ("prompt engineer", 0.9),
    ],
}

# Static compound signals extracted once at module load, preserved on every
# dynamic update.  These solve structural language patterns
# ("design a system" = coding, not creative) that TF-IDF cannot discover
# from single-word tokenization.
_STATIC_COMPOUND_SIGNALS: dict[str, list[tuple[str, float]]] = {
    task_type: [(kw, w) for kw, w in keywords if " " in kw]
    for task_type, keywords in _TASK_TYPE_SIGNALS.items()
}

# B6: single-word baseline. ``set_task_type_signals()`` falls back to this
# snapshot when a dynamic payload has no entry for a given task_type so the
# B1/A8 defaults ("sqlalchemy", "fastapi", "factory", "session", "cli",
# "daemon", ...) survive warm-path partial extractions. A partial extraction
# (TF-IDF crossing the 30-sample threshold on, say, ``writing`` only) would
# otherwise rebuild the table without any single-word entry for the other
# task types — the 2026-04-21 live SQL session-factory prompt scored
# ``coding=0.0`` exactly because of this regression.
_STATIC_SINGLE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    task_type: [(kw, w) for kw, w in keywords if " " not in kw]
    for task_type, keywords in _TASK_TYPE_SIGNALS.items()
}

# --- A4: Confidence-gated LLM fallback thresholds ---
# T1.7 — gate tightened so we stop paying Haiku tokens on prompts where the
# heuristic ALREADY made a confident call.  Pre-fix: 0.5/0.2 caused every
# classification on the project's own corpus to fall through to Haiku
# (4 of 4 task_type_telemetry rows = ``haiku_fallback``, none from the
# static path).  Post-fix: 0.40 confidence + 0.10 margin — Haiku only fires
# when the heuristic genuinely can't tell two task types apart.  The
# B2 ``has_technical_nouns`` rescue + technical-verb disambiguation (A2)
# already cover the high-leverage code-aware cases at confidence 0.6+, so
# this tighter gate doesn't sacrifice quality on the live workload.
_LLM_CLASSIFICATION_CONFIDENCE_GATE = 0.40  # heuristic confidence below this triggers check
_LLM_CLASSIFICATION_MARGIN_GATE = 0.10      # margin between top 2 categories below this triggers LLM

# --- Technical verb + noun disambiguation (A2) ---
# When a technical verb appears with a technical noun in the first sentence,
# the prompt is almost certainly coding-related, even if "design" or "create"
# triggered the creative category.  Checked post-classification.
_TECHNICAL_VERBS = frozenset({
    "design", "create", "build", "set", "configure", "add", "implement",
    "refactor", "debug", "migrate", "deploy", "test", "develop",
})
_TECHNICAL_NOUNS = frozenset({
    "system", "service", "api", "endpoint", "schema", "database",
    "middleware", "pipeline", "queue", "cache", "scheduler", "server",
    "backend", "frontend", "module", "library", "framework", "migration",
    "table", "index", "model", "route", "handler", "worker",
    # A8: unambiguous engineering artifacts. Added after the live "Fastapi
    # Log Tail CLI" prompt classified as creative (score 1.4 from "design"
    # alone) — the disambiguation gate was blind to CLI-family nouns.
    # Conservative additions only — generic words like "tool"/"script" are
    # excluded because they legitimately appear in creative briefs.
    "cli", "daemon", "binary",
    # B1: OOP-pattern nouns and framework identifiers. Live reference:
    # "Design a SQLAlchemy async session factory ... for FastAPI". The
    # creative "design" verb needs to flip via A2 disambiguation and
    # required a technical-noun hit inside the first sentence, 4-word
    # window. ``session`` / ``factory`` are OOP patterns (DB sessions,
    # factory functions). Framework names are unambiguous tech context —
    # no creative-writing prompt says "design a FastAPI".
    # Explicitly NOT added: ``app``, ``tool``, ``client`` — all three
    # have legitimate creative-writing use.
    "factory", "session", "sqlalchemy", "fastapi", "django", "flask",
    # B3 (2026-04-25 validation cycle): async / concurrency primitives.
    # All zero-non-code-legitimacy — no creative-writing prompt says
    # "audit the asyncio gather" or "trace the coroutine cancellation".
    # Live reference: "Audit the asyncio.gather error handling in our
    # warm-path Phase 4 — find race conditions ..." matched no technical
    # nouns and was demoted to the ``knowledge_work`` enrichment profile,
    # silently skipping curated retrieval + strategy intelligence + pattern
    # injection on a clearly-code-aware prompt about a linked codebase.
    # Conservative additions — ``async`` alone is excluded because adjective
    # use is too broad; ``await`` is excluded because it has prose meaning.
    "asyncio", "coroutine", "eventloop", "mutex", "semaphore", "deadlock",
    # DB transaction primitive — ``savepoint`` is unambiguous (no creative
    # legitimacy), unlike ``transaction`` which is overloaded with finance.
    "savepoint",
    # B4 (cycle 9 validation): vocabulary scope expansion across
    # commonly-audited tech categories.  Every term below is unambiguous —
    # zero overlap with creative-writing legitimacy.  Specifically excluded:
    # ``test`` / ``build`` / ``component`` / ``metric`` / ``hook`` —
    # these all have prose use ("test the waters", "build a relationship",
    # "metric system", "off the hook") and would over-trigger.  Brand
    # names + technical compounds get in; generic engineering verbs and
    # nouns stay out.
    #
    # Container / orchestration / IaC.  Dropped terms with prose use:
    # ``container`` (shipping), ``image`` (photo), ``deployment``
    # (military/marketing), ``ingress`` (architecture).  Kept brand
    # names + compound terms with zero prose legitimacy.
    "dockerfile", "docker-compose", "kubectl", "helm", "terraform",
    "ansible", "containerd", "podman",
    "nginx", "haproxy", "traefik", "envoy", "istio",
    "healthcheck",
    # OAuth / auth / crypto primitives.  Acronyms have zero prose use.
    "oauth", "oidc", "saml", "openid", "fernet", "jwt", "csrf", "xss",
    "bcrypt", "argon2", "scrypt", "mtls",
    # Frontend frameworks.  Dropped: ``react`` (chemistry), ``vue`` (view
    # in French), ``angular`` (math/anatomy).  Kept compound brands.
    "svelte", "sveltekit", "nextjs", "tailwind", "vite",
    # Backend frameworks (extension of the FastAPI line above).
    # Dropped: ``celery`` (vegetable).
    "uvicorn", "gunicorn", "starlette", "wsgi", "asgi",
    # Datastores + queues + caches.  Dropped: ``mongo`` (mongoose family),
    # ``kafka`` (author).
    "postgresql", "mongodb", "redis", "memcached",
    "elasticsearch", "rabbitmq", "pulsar",
    # Observability / tracing brand names.  Dropped: ``prometheus``
    # (mythology), ``loki`` (mythology), ``sentry`` (military), ``jaeger``
    # (German for hunter).  Kept compound + product-specific.
    "opentelemetry", "datadog",
    # Test runners.  Dropped: ``mocha`` (drink), ``cypress`` (tree).
    "pytest", "jest", "vitest", "playwright",
    # Concurrency runtime primitives (extends ``asyncio`` line above).
    # Dropped: ``channel`` (TV / communication), ``lifecycle`` (career),
    # ``lambda`` (math), ``rayon`` (fabric).
    "goroutine", "tokio",
})

# Pre-compiled word-boundary patterns for task_type keywords.  Built once at
# import time to avoid recompilation in hot loops.  Domain patterns are
# managed by ``DomainSignalLoader._precompile_patterns()``.
_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {}

# Pre-compiled strippers for ``extract_first_sentence()`` — applied in order
# before the ``.?!`` terminator split.  Code fences and markdown tables at
# the top of a prompt would otherwise pollute ``first_sentence`` with
# technical-noun content that earns the 2x positional boost despite not
# reflecting the user's intent.  (#7)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_MD_TABLE_ROW_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")


# Inline-backtick content classified as a "real code reference" gets
# unwrapped (content preserved); other backtick spans are stripped to
# blank space (legacy behaviour).  A real code reference is signalled by:
#   - a path separator ``/`` (file path: ``backend/app/...``)
#   - a recognised source-file extension (``.py``, ``.svelte``, ``.ts``,
#     ``.tsx``, ``.js``, ``.jsx``, ``.yml``, ``.yaml``, ``.json``, ``.md``,
#     ``.go``, ``.rs``, ``.java``, ``.kt``, ``.cs``, ``.rb``, ``.php``)
#   - snake_case (``_``) — guaranteed code identifier syntax
# Anything else (e.g. ``\`pipeline.schema.endpoint.database.api\``` —
# concatenated technical nouns with no real-file shape) is treated as a
# code-dump and stripped, preserving the original keyword-boost
# protection while restoring the B2 rescue on real file mentions.
_CODE_FILE_EXT_RE = re.compile(
    r"\.(?:py|svelte|tsx?|jsx?|ya?ml|json|md|go|rs|java|kt|cs|rb|php)\b",
    re.IGNORECASE,
)


def _looks_like_code_reference(content: str) -> bool:
    """Return True if the content looks like a real file path / identifier."""
    if "/" in content:
        return True
    if "_" in content:
        return True
    if _CODE_FILE_EXT_RE.search(content):
        return True
    return False


def _strip_code_and_tables(text: str) -> str:
    """Remove triple-backtick fences and pipe-delimited markdown table rows;
    SELECTIVELY unwrap inline-backtick spans (only those that look like
    real code references — file paths, module.method tokens, snake_case).

    Triple-backtick fences and tables are stripped entirely (replaced with
    a single space so adjacent prose words don't fuse) — they typically
    contain code dumps that pollute first-sentence keyword analysis.

    Inline backtick-wrapped identifier spans get differentiated treatment:
      - Real code refs (paths, file extensions, snake_case) are UNWRAPPED
        so the B2 ``has_technical_nouns`` rescue can see them.  Pre-fix,
        cycle-9 prompts 1/2/4 (Heatmap, SQL, OAuth) had their file paths
        deleted entirely, demoting them to ``knowledge_work`` profile and
        skipping codebase context on clearly-code-aware prompts.
      - Concept-strings (e.g. a backtick span containing
        ``pipeline.schema.endpoint.database.api`` — a stack of technical
        nouns with no real-file shape) stay STRIPPED because their content
        was never the user's intent — they're keyword dumps that distort
        the first-sentence boost.

    Live regressions covered:
      - cycle-9 prompt 4 (GitHub OAuth audit, 2 file paths in backticks):
        unwrap → code_aware → codebase context delivered.
      - test_inline_backticks_stripped_from_first_sentence (concept-stack
        in backticks + ``evaluate and audit`` verb): strip → analysis
        task_type preserved as designed.
    """
    text = _CODE_FENCE_RE.sub(" ", text)

    def _inline_handler(m: re.Match) -> str:
        body = m.group(0).strip("`")
        return body if _looks_like_code_reference(body) else " "

    text = _INLINE_CODE_RE.sub(_inline_handler, text)
    text = _MD_TABLE_ROW_RE.sub(" ", text)
    return text


def extract_first_sentence(prompt_lower: str) -> str:
    """Return the first sentence of ``prompt_lower`` for keyword-boost scoring.

    Strips code / tables then splits on the first ``.?!`` terminator that is
    followed by whitespace or end-of-string. The trailing-whitespace lookahead
    distinguishes sentence terminators from interior dots in identifier
    syntax (``EmbeddingService.embed_single``, ``main.py``, ``module.method()``)
    which previously truncated the boundary at the first dot — losing every
    downstream technical noun.

    Exposed publicly so ``context_enrichment.py`` and any future callers
    share one boundary semantic with the heuristic classifier.
    """
    return re.split(
        r"[.?!](?=\s|$)", _strip_code_and_tables(prompt_lower), maxsplit=1,
    )[0]


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


# A4: Task types whose singles came from a *current* TF-IDF extraction run
# (i.e. crossed the MIN_SAMPLES threshold). Signals loaded from a disk cache
# at process boot do NOT populate this set — they look identical to freshly-
# extracted signals in the merged table, but they are not proof of live
# learning for telemetry purposes.
_TASK_TYPE_EXTRACTED: set[str] = set()


def set_task_type_signals(
    dynamic_signals: dict[str, list[tuple[str, float]]],
    extracted_task_types: set[str] | None = None,
) -> None:
    """Merge dynamic single-word signals with static compound signals.

    Called by warm-path Phase 4.75 and backend/MCP lifespan.  Validates
    input, merges, clears + rebuilds pattern cache.  Silently no-ops on
    empty input or malformed payload so a single bad discovery run never
    destroys the baseline classifier.

    A4: ``extracted_task_types`` names the task types whose signals come
    from a *current* TF-IDF extraction run (they crossed the 30-sample
    threshold in this process). Passing ``None`` — the default used by
    cache-warmup loaders at boot — clears the extraction set so callers
    never mistake a cached table for live learning.

    B6: task types absent from ``dynamic_signals`` fall back to the
    ``_STATIC_SINGLE_SIGNALS`` baseline captured at module load so single-
    word B1/A8 defaults survive partial warm-path merges. A task type that
    appears in ``dynamic_signals`` has its static singles replaced by the
    dynamic payload (the extraction claim is: "these are the current
    singles for this type"); unextracted types keep their defaults.
    """
    if not dynamic_signals:
        logger.warning("set_task_type_signals: empty dict — keeping current signals")
        return
    for task_type, keywords in dynamic_signals.items():
        if not isinstance(keywords, list):
            logger.warning("set_task_type_signals: invalid keywords for %s — aborting", task_type)
            return
    # B6: fall back to ``_STATIC_SINGLE_SIGNALS`` when a task type has no
    # dynamic payload. Partial warm-path extractions (TF-IDF crossed the
    # 30-sample threshold for some types but not others) must not wipe the
    # single-word defaults for the unextracted types — that regression
    # caused the live 2026-04-21 SQL session-factory prompt to score
    # ``coding=0.0`` and drift to ``creative``.
    merged: dict[str, list[tuple[str, float]]] = {}
    all_task_types = (
        set(dynamic_signals.keys())
        | set(_STATIC_COMPOUND_SIGNALS.keys())
        | set(_STATIC_SINGLE_SIGNALS.keys())
    )
    for task_type in all_task_types:
        compounds = _STATIC_COMPOUND_SIGNALS.get(task_type, [])
        if task_type in dynamic_signals:
            singles = dynamic_signals[task_type]
        else:
            singles = _STATIC_SINGLE_SIGNALS.get(task_type, [])
        merged[task_type] = compounds + singles
    global _TASK_TYPE_SIGNALS, _TASK_TYPE_EXTRACTED
    _TASK_TYPE_SIGNALS = merged
    _TASK_TYPE_EXTRACTED = set(extracted_task_types) if extracted_task_types else set()
    _precompile_keyword_patterns()
    logger.info(
        "TaskTypeSignals: merged %d task types, %d total keywords "
        "(%d compound + %d dynamic, extracted=%s)",
        len(merged), sum(len(v) for v in merged.values()),
        sum(len(v) for v in _STATIC_COMPOUND_SIGNALS.values()),
        sum(len(v) for v in dynamic_signals.values()),
        sorted(_TASK_TYPE_EXTRACTED) or "∅",
    )


def get_task_type_signals() -> dict[str, list[tuple[str, float]]]:
    """Return the current merged task-type signal table (read-only view)."""
    return _TASK_TYPE_SIGNALS


def get_static_compound_signals() -> dict[str, list[tuple[str, float]]]:
    """Return the static compound signal table (invariant across dynamic updates)."""
    return _STATIC_COMPOUND_SIGNALS


def task_type_has_dynamic_signals(task_type: str) -> bool:
    """Return True iff ``task_type`` was in the most recent extraction run.

    A4: this is the authoritative check for whether the classifier is
    operating on live TF-IDF signals vs bootstrap defaults. The merged
    signal table alone cannot answer this — a cache-warmup load at boot
    populates the same table shape as a fresh extraction.
    """
    return task_type in _TASK_TYPE_EXTRACTED


def reset_task_type_extracted() -> None:
    """Test helper — wipe the extraction set. Not used in production code."""
    global _TASK_TYPE_EXTRACTED
    _TASK_TYPE_EXTRACTED = set()


def score_category(
    prompt_lower: str,
    first_sentence: str,
    keywords: list[tuple[str, float]],
) -> float:
    """Score a category by weighted keyword presence with positional boost.

    Uses pre-compiled word-boundary patterns to avoid false positives
    (e.g. "class" should not match "classification").  Multi-word keywords
    (e.g. "system prompt") use simple substring search since ``\\b`` would
    not match internal spaces correctly.  First-sentence matches get a 2x
    boost so the intent in the leading clause dominates incidental mentions
    buried later in the prompt.
    """
    score = 0.0
    for keyword, weight in keywords:
        kw = keyword.lower()
        if " " in kw:
            # Multi-word keywords: substring match (word-boundary would fail)
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
            multiplier = 2.0 if found_in_first else 1.0
            score += weight * multiplier
    return score


def classify_task_type(
    prompt_lower: str,
    first_sentence: str,
    signals: dict[str, list[tuple[str, float]]] | None = None,
) -> tuple[str, float, dict[str, float]]:
    """Score all categories and return ``(best_category, confidence, all_scores)``.

    When ``signals`` is None, uses the module-level ``_TASK_TYPE_SIGNALS``
    (which may have been dynamically merged by ``set_task_type_signals()``).
    """
    if signals is None:
        signals = _TASK_TYPE_SIGNALS
    scores: dict[str, float] = {}
    for category, keywords in signals.items():
        scores[category] = score_category(prompt_lower, first_sentence, keywords)
    if not scores or max(scores.values()) == 0:
        logger.debug("classify_task_type: all scores zero — fallback to 'general'")
        return "general", 0.0, scores
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    logger.debug(
        "classify_task_type: best=%s confidence=%.2f runner_up=%s",
        best, min(1.0, scores[best]),
        sorted(scores.items(), key=lambda x: x[1], reverse=True)[1][0]
        if len(scores) > 1 else "n/a",
    )
    return best, min(1.0, scores[best]), scores


def check_technical_disambiguation(first_sentence: str) -> bool:
    """Check if the first sentence contains a technical verb + noun pair.

    Scans for any verb from ``_TECHNICAL_VERBS`` followed within 4 words by
    a noun from ``_TECHNICAL_NOUNS``.  Handles articles/prepositions in
    between (e.g. "design a REST api", "build the caching system").  Words
    are stripped of trailing punctuation before matching.
    """
    words = [w.strip(".,;:!?()[]{}\"'") for w in first_sentence.split()]
    for i, word in enumerate(words):
        if word in _TECHNICAL_VERBS:
            for j in range(i + 1, min(i + 5, len(words))):
                if words[j] in _TECHNICAL_NOUNS:
                    logger.debug(
                        "tech_disambig: verb=%r noun=%r distance=%d",
                        word, words[j], j - i,
                    )
                    return True
    return False


# B4 (2026-04-25 cycle 2): Python-identifier-syntax patterns. snake_case
# (``link_repo``, ``_spawn_bg_task``, ``persist_optimization``) and
# PascalCase compounds (``EmbeddingService``, ``TaxonomyEngine``) carry
# zero non-code legitimacy — natural prose never produces these forms.
# Match on the ORIGINAL (un-lowercased) whitespace token so casing
# stays intact for the Pascal check.
_SNAKE_CASE_RE = re.compile(r"^_?[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
# Any leading ``_``, lowercase root, and 1+ underscore-prefixed
# alphanumeric segments — so ``link_repo`` (1 separator) AND
# ``_spawn_bg_task`` (3 separators) both qualify.
_PASCAL_CASE_RE = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+){1,}$")
# Two+ adjacent capital-led words (``EmbeddingService``, ``TaxonomyEngine``)
# — sentence-start single-capital words (``Today``) intentionally excluded.


def _looks_like_identifier(token: str) -> bool:
    """Return True for tokens that match Python identifier syntax.

    Splits on interior `.` so module-method tokens
    (``TaxonomyEngine.persist_optimization``) match either component.

    snake_case (e.g. ``link_repo``, ``_spawn_bg_task``) requires
    underscores by definition — these are zero-non-code-legitimacy
    syntactic markers and always count.

    PascalCase (e.g. ``EmbeddingService``, ``TaxonomyEngine``) is
    syntactically just two capital-led words concatenated, which also
    matches plain prose brand names (``JavaScript``, ``TypeScript``,
    ``GitHub``, ``YouTube``, ``McDonalds``, ``PostScript``…). To avoid
    false positives on prose mentions of those brands, PascalCase
    counts only when the ORIGINAL whitespace token carries a
    **structural marker** (``.``, ``-``, ``/``) signalling actual code
    context — ``EmbeddingService.embed_single`` fires (dot present),
    ``backend/app/EmbeddingService`` fires (slash present), but bare
    ``JavaScript`` does not.

    Conservative on Pascal (requires 2+ capitalized words) so
    sentence-start single-capital words (``Today``) are out by
    construction. The B2 rescue path is further gated by
    ``repo_linked=True`` at the call site, bounding the blast radius
    of any remaining marginal false positive.
    """
    if not token:
        return False
    has_struct_marker = any(c in "._-/" for c in token)
    for piece in token.split("."):
        if not piece:
            continue
        if _SNAKE_CASE_RE.match(piece):
            return True
        if _PASCAL_CASE_RE.match(piece) and has_struct_marker:
            return True
    return False


def has_technical_nouns(first_sentence: str) -> bool:
    """Return True if the first sentence contains any ``_TECHNICAL_NOUNS`` word
    OR any token matching Python identifier syntax (snake_case / PascalCase).

    Looser signal than :func:`check_technical_disambiguation` — does NOT
    require a paired technical verb. Used by the B2 enrichment-profile
    rescue: an analysis/creative/general prompt that still references a
    framework or technical artifact ("audit the routing pipeline", "review
    the websocket middleware") is almost certainly about the linked
    codebase and should get ``code_aware`` context even though the
    task_type stays non-coding.

    Two parallel signals fire here:

    1. **Vocabulary hit.** Words are lowercased, stripped of trailing
       punctuation, and SPLIT on interior dots / hyphens so module-method
       tokens (``asyncio.gather``) and kebab-case identifiers
       (``async-session``) match their constituent technical noun.

    2. **Identifier syntax.** Multi-segment snake_case (``_spawn_bg_task``,
       ``link_repo_callback``) and PascalCase compounds (``EmbeddingService``)
       are zero-non-code-legitimacy markers — natural prose never produces
       them. Catches the live regression where a prompt about a linked
       codebase used ONLY identifier names without any nouns from the
       keyword set, demoting to ``knowledge_work``.
    """
    expanded: list[str] = []
    has_identifier = False
    for raw in first_sentence.split():
        if _looks_like_identifier(raw.strip(".,;:!?()[]{}\"'")):
            has_identifier = True
        # Vocabulary path: lowercase + strip + interior `.`/`-`/`/` split
        # so file paths (``backend/app/services/...``), kebab-case
        # (``cache-aware``), and module-method tokens (``asyncio.gather``)
        # all contribute their constituent tokens to the noun match.
        word = raw.lower().strip(".,;:!?()[]{}\"'")
        if word:
            expanded.append(word)
        for sub in re.split(r"[.\-/]", word):
            if sub and sub != word:
                expanded.append(sub)
    if has_identifier:
        return True
    return any(w in _TECHNICAL_NOUNS for w in expanded)


# Task types where structural code evidence in the prompt should override
# semantically-vague signals like "create"/"design"/"concept" that bias the
# classifier toward ``creative``/``writing``. The rescue intentionally
# does NOT cover ``analysis`` or ``data`` — those types frequently
# co-occur with code identifiers in legitimate ways (auditing a function,
# extracting a column, etc.) and shouldn't be silently rewritten.
_TASK_TYPE_RESCUE_TARGETS: frozenset[str] = frozenset({"creative", "writing"})


def rescue_task_type_via_structural_evidence(
    task_type: str, raw_prompt: str,
) -> tuple[str, str | None]:
    """Override ``creative``/``writing`` with ``coding`` when structural code evidence is present.

    Mirrors the B2 enrichment-profile rescue (``code_aware`` profile when
    ``has_technical_nouns(first_sentence)`` returns True on a linked-repo
    prompt) but applies to ``task_type`` itself. The classifier's
    ``creative`` signals (``"create" 0.5``, ``"design" 0.7``,
    ``"concept" 0.6``) are deliberately broad so that prose prompts about
    invention/concepts route correctly — but they collide with
    code-aware vocabulary like ``"create a class"``, ``"design the
    schema"``, ``"the lifecycle concept"``. When the prompt's first
    sentence carries unambiguous syntactic markers of code
    (snake_case identifiers, PascalCase classes with structural
    separators, dotted module-method tokens), structural evidence
    should beat semantic vibes.

    Cycle-3/4 evidence (2026-04-25): user reported intermittent
    ``"Background Task Lifecycle Tracking"``-style intent labels
    landing under ``creative`` despite obvious code intent. The
    pattern is reproducible whenever a prompt's verbs lean toward
    ``"design"``/``"create"`` AND the surface text doesn't include
    enough strong coding compound signals to outweigh the creative
    score.

    Args:
        task_type: The classifier's current task type (heuristic or
            LLM-returned).
        raw_prompt: The full prompt text. Only the first sentence is
            inspected — that's the convention shared with
            ``has_technical_nouns`` and ``select_enrichment_profile``.

    Returns:
        Tuple of ``(possibly_overridden_task_type, rescue_reason)``.
        ``rescue_reason`` is non-None only when an override fired,
        suitable for logging and observability tagging. The reason
        names the trigger (``"snake_case identifier"``,
        ``"PascalCase identifier"``, ``"technical noun"``) so
        downstream traces can audit the rescue cleanly.
    """
    if task_type not in _TASK_TYPE_RESCUE_TARGETS:
        return (task_type, None)

    # Important: extract the first sentence from the ORIGINAL prompt
    # (case-preserving) — ``_looks_like_identifier()`` needs the original
    # casing to detect PascalCase. The shared ``extract_first_sentence``
    # helper expects an already-lowercased input by name; using it on
    # the raw prompt works because the boundary regex only inspects
    # punctuation, not letter case. (The pre-existing
    # ``has_technical_nouns`` codepath does lowercase first and so silently
    # loses PascalCase detection — we don't share its caller pattern here.)
    first_sentence_original = extract_first_sentence(raw_prompt)
    if not first_sentence_original:
        return (task_type, None)
    first_sentence_lower = first_sentence_original.lower()

    # Layer 1: syntactic identifier scan on ORIGINAL-case tokens.
    syntactic_reason: str | None = None
    for raw in first_sentence_original.split():
        token = raw.strip(".,;:!?()[]{}\"'")
        if not token:
            continue
        has_struct_marker = any(c in "._-/" for c in token)
        for piece in token.split("."):
            if not piece:
                continue
            if _SNAKE_CASE_RE.match(piece):
                syntactic_reason = f"snake_case identifier '{piece}'"
                break
            if _PASCAL_CASE_RE.match(piece) and has_struct_marker:
                syntactic_reason = (
                    f"PascalCase identifier '{piece}' with structural marker"
                )
                break
        if syntactic_reason:
            break

    if syntactic_reason:
        return ("coding", f"{task_type}→coding rescue ({syntactic_reason})")

    # Layer 2: technical-noun vocabulary scan on lowered-case tokens.
    # Mirrors the vocab path inside ``has_technical_nouns`` (lowercase +
    # strip + interior `.`/`-`/`/` split), without conflating with the
    # syntactic check above.
    expanded: list[str] = []
    for raw in first_sentence_lower.split():
        word = raw.strip(".,;:!?()[]{}\"'")
        if word:
            expanded.append(word)
        for sub in re.split(r"[.\-/]", word):
            if sub and sub != word:
                expanded.append(sub)
    matched_noun = next(
        (w for w in expanded if w in _TECHNICAL_NOUNS), None,
    )
    if matched_noun:
        return (
            "coding",
            f"{task_type}→coding rescue (technical noun '{matched_noun}')",
        )

    return (task_type, None)


async def classify_with_llm(
    raw_prompt: str,
    db: AsyncSession,
    *,
    provider: Any | None = None,
) -> tuple[str, str] | None:
    """Fast LLM classification fallback using Haiku (A4 gate).

    Returns ``(task_type, domain)`` or ``None`` on failure.  Only called
    when heuristic confidence is ambiguous — minimal prompt (~500 input
    tokens, ~20 output tokens).  Wrapped in ``call_provider_with_retry``
    so transient rate-limit / overload errors retry once; non-retryable
    errors and final-attempt failures return None and the caller degrades
    to the heuristic result.

    The ``db`` parameter is currently unused but retained for future
    affordances (e.g. domain-specific classifier seeds from the taxonomy).
    """
    try:
        from pydantic import BaseModel as _BaseModel

        from app.config import settings
        from app.providers.base import call_provider_with_retry

        if provider is None:
            logger.warning("llm_classification_fallback: no provider supplied, skipping A4 fallback")
            return None

        # Build known domains list from the signal loader when available.
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

        result = await call_provider_with_retry(
            provider,
            model=settings.MODEL_HAIKU,
            system_prompt="You are a prompt classifier.",
            user_message=prompt_text,
            output_format=_ClassificationResult,
            max_tokens=100,
        )

        task_type = result.task_type
        domain = result.domain

        valid_types = {"coding", "writing", "analysis", "creative", "data", "system", "general"}
        if task_type not in valid_types:
            task_type = "general"

        logger.info(
            "llm_classification_result: task_type=%s domain=%s",
            task_type, domain,
        )

        try:
            from app.models import TaskTypeTelemetry
            telemetry = TaskTypeTelemetry(
                raw_prompt=raw_prompt,
                task_type=task_type,
                domain=domain,
                source="haiku_fallback",
            )
            db.add(telemetry)
            logger.debug("Persisted Haiku fallback to TaskTypeTelemetry")
        except Exception as tel_exc:
            logger.warning("Failed to persist TaskTypeTelemetry: %s", tel_exc)

        return task_type, domain

    except Exception:
        logger.debug("llm_classification_fallback failed", exc_info=True)
        return None


__all__ = [
    "LLM_CLASSIFICATION_CONFIDENCE_GATE",
    "LLM_CLASSIFICATION_MARGIN_GATE",
    "check_technical_disambiguation",
    "classify_task_type",
    "classify_with_llm",
    "extract_first_sentence",
    "get_static_compound_signals",
    "get_task_type_signals",
    "has_technical_nouns",
    "reset_task_type_extracted",
    "score_category",
    "set_task_type_signals",
    "task_type_has_dynamic_signals",
]

# Public re-exports of the private gate constants (orchestrator needs them
# to evaluate the A4 fallback condition).
LLM_CLASSIFICATION_CONFIDENCE_GATE = _LLM_CLASSIFICATION_CONFIDENCE_GATE
LLM_CLASSIFICATION_MARGIN_GATE = _LLM_CLASSIFICATION_MARGIN_GATE
