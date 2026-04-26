"""Repo-relevance gate (B0) for context enrichment.

Single-threshold embedding relevance check between a prompt and the linked
repo's architectural synthesis.  When the prompt is clearly unrelated (same
tech stack, different project), this gate skips codebase-context injection
so the optimizer doesn't inherit noise from the wrong repo.

Extracted from ``context_enrichment.py`` (Phase 3A).  Public API is preserved
via re-exports in ``context_enrichment.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# Cap for stride-sampled file paths appended to the repo-relevance anchor.
# ~100 lines of paths keeps MiniLM's 512-token window under pressure while
# still covering every major subtree of a medium-sized repo (370 files in
# the reference index).
_MAX_ANCHOR_PATHS = 100

_GENERIC_TERMS = frozenset({
    # Architecture / structure
    "service", "services", "model", "models", "controller", "handler",
    "module", "modules", "interface", "component", "components",
    "factory", "provider", "middleware", "wrapper", "manager",
    "backend", "frontend", "system", "systems", "application",
    "project", "projects", "framework", "library", "package",
    # CRUD / data
    "create", "read", "update", "delete", "query", "filter",
    "request", "response", "result", "results", "payload",
    "field", "fields", "column", "columns", "table", "tables",
    "record", "records", "entry", "entries", "item", "items",
    "database", "migration", "migrations",
    # Files / config
    "file", "files", "directory", "path", "config", "configuration",
    "setting", "settings", "option", "options", "parameter",
    # Code constructs
    "function", "method", "class", "instance", "object", "variable",
    "value", "values", "return", "import", "export", "async", "await",
    "callback", "promise", "decorator", "annotation",
    # HTTP / API
    "endpoint", "route", "router", "server", "client", "port", "host",
    "header", "headers", "body", "status", "error", "errors",
    "json", "yaml", "html", "text", "string", "number", "boolean",
    # Common actions
    "init", "start", "stop", "setup", "build", "test", "tests",
    "check", "validate", "parse", "format", "convert", "process",
    "load", "save", "send", "fetch", "push", "pull",
    # Generic nouns
    "name", "title", "description", "content", "type", "types",
    "state", "data", "info", "meta", "context", "source",
    "default", "optional", "required", "enabled", "disabled",
    "base", "core", "utils", "helpers", "common", "shared",
    "user", "users", "admin", "role", "session", "token",
    "list", "page", "pagination", "offset", "limit", "total",
    "schema", "schemas", "validator", "validators",
    "level", "mode", "version", "index", "count",
    "event", "events", "action", "actions", "task", "tasks",
    "null", "none", "true", "false", "undefined",
    "logging", "logger", "debug", "warning",
    # Software lifecycle
    "testing", "deploy", "deployment", "release", "staging",
    "template", "templates", "phase", "only", "first", "never",
    "tracking", "active", "calls", "from", "with", "turn",
})


def extract_domain_vocab(
    synthesis: str,
    file_paths: list[str] | None = None,
) -> frozenset[str]:
    """Extract domain-specific vocabulary from synthesis + indexed file paths.

    Two-source fusion:

    * **Synthesis tokens** — freq ``>= 3`` filter.  The synthesis is a
      narrative architectural summary where coherent domain terms repeat
      naturally; the frequency floor separates those from incidental
      mentions.
    * **Path tokens** — freq ``>= 1``.  File paths are already
      repo-specific (they name the actual modules and components), so a
      single occurrence is enough evidence that the term is part of the
      codebase vocabulary.

    Both sources are passed through the generic-term (:data:`_GENERIC_TERMS`)
    and tech-stack (imported from ``divergence_detector._TECH_VOCABULARY``)
    filters, then unioned.  Returns a frozenset of domain-specific terms
    characterizing the linked repo.

    The path-token branch materially improves matching for component-level
    prompts (e.g. ``"Clusters Navigation Panel"`` matching
    ``frontend/.../ClustersNavigationPanel.svelte``) that synthesis prose
    alone is too sparse to surface.
    """
    from app.services.divergence_detector import _TECH_VOCABULARY

    tech_aliases: set[str] = set()
    for techs in _TECH_VOCABULARY.values():
        for aliases in techs.values():
            tech_aliases.update(aliases)

    # Synthesis tokens (freq >= 3)
    synth_vocab: set[str] = set()
    if synthesis:
        words = re.findall(r"\b[a-z][a-z_]{3,}\b", synthesis.lower())
        freq = Counter(words)
        synth_vocab = {
            w for w, c in freq.items()
            if c >= 3 and w not in _GENERIC_TERMS and w not in tech_aliases
        }

    # Path tokens (freq >= 1 — paths are already repo-specific).
    # `/`, `.`, `-` are word boundaries so CamelCase components inside
    # e.g. ``ClustersNavigationPanel.svelte`` still split on `.` boundaries
    # into individual tokens after lowercasing.
    path_vocab: set[str] = set()
    if file_paths:
        for path in file_paths:
            tokens = re.findall(r"\b[a-z][a-z_]{3,}\b", path.lower())
            for t in tokens:
                if t not in _GENERIC_TERMS and t not in tech_aliases:
                    path_vocab.add(t)

    return frozenset(synth_vocab | path_vocab)


# Boost weights for the additive relevance gate (see compute_repo_relevance).
# Calibrated so a confident identifier match alone (cosine ≈ 0.05) lifts the
# score above REPO_RELEVANCE_FLOOR=0.15.  Domain-overlap boost is small per
# match but capped, so large vocabularies don't overwhelm an unrelated prompt.
_IDENTIFIER_MATCH_BOOST = 0.10
_DOMAIN_OVERLAP_BOOST_PER_MATCH = 0.01
_DOMAIN_OVERLAP_CAP = 10  # max additive +0.10 from overlap

# Min length for an identifier sub-token to be a credible file-path match.
# Below 4 chars produces noise (`api`, `db`, `id` show up everywhere); 4
# admits `auth`, `repo`, `cache`, etc. without false positives.
_MIN_IDENTIFIER_TOKEN_LEN = 4


def _pascal_to_snake(token: str) -> str:
    """Convert ``PascalCaseIdentifier`` to ``pascal_case_identifier``.

    Handles consecutive caps gracefully (``HTTPSConnection`` →
    ``https_connection``).  Used to bridge the gap between prompt-side
    PascalCase mentions of a class and the snake_case file path that
    holds that class.
    """
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", token)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _check_identifier_match(
    raw_prompt: str, file_paths: list[str] | None,
) -> tuple[bool, list[str]]:
    """Return ``(matched, top_hits)`` if any prompt identifier hits a file path.

    A "credible" identifier match means the prompt explicitly named a class,
    module, or file that exists in the linked repo — the strongest possible
    signal that codebase context belongs in this request, regardless of what
    the cosine embedding says about the prompt as a whole.

    Algorithm:
    - For each whitespace-separated token, split on interior ``.``/``-``/``/``
      (so ``DomainSignalLoader.qualifier_embedding_cache`` decomposes).
    - Convert PascalCase sub-tokens to snake_case.
    - Filter sub-tokens shorter than 4 chars (avoid noise).
    - Substring-match against the lowercased file_paths blob.

    Conservative on false positives: requires the snake_case form to appear
    in a path (filename or directory), not just any vocabulary overlap.
    """
    if not file_paths:
        return False, []
    paths_blob = "\n".join(p.lower() for p in file_paths)

    hits: list[str] = []
    for raw in raw_prompt.split():
        token = raw.strip(".,;:!?()[]{}\"'`")
        if not token:
            continue
        for sub in re.split(r"[.\-/]", token):
            if not sub:
                continue
            snake = _pascal_to_snake(sub)
            if len(snake) < _MIN_IDENTIFIER_TOKEN_LEN:
                continue
            if snake in paths_blob:
                hits.append(snake)
                if len(hits) >= 5:
                    return True, hits
    return bool(hits), hits


async def compute_repo_relevance(
    raw_prompt: str,
    explore_synthesis: str,
    embedding_service: Any,
    repo_full_name: str | None = None,
    file_paths: list[str] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Single-threshold relevance between a prompt and the linked repo.

    The anchor text is assembled from three layers so the embedding
    captures the project identity, its narrative architecture, **and** its
    component-level surface area:

    1. ``"Project: {repo_full_name}"`` — identity prefix, disambiguates two
       repos with similar tech-stack signatures.
    2. ``{explore_synthesis}`` — Haiku-generated narrative summary
       (architectural overview, ~3K chars).
    3. ``"Components:\\n{joined file paths}"`` — a stride-sampled subset of
       indexed file paths (cap 100, for MiniLM's 512-token window), giving
       the embedding explicit module-level signal.  Without this layer,
       component-level prompts (e.g. *"Clusters Navigation Panel"*) often
       fall below the floor because synthesis prose describes the system
       in aggregate, not the individual files by name.

    Decision is **additive**.  Pure cosine MiniLM is structurally suppressed
    on short prompts that name a single class+method (e.g. *"Audit
    DomainSignalLoader.qualifier_embedding_cache"* — 255 chars vs a 50K-char
    anchor), even when the prompt is unambiguously about the linked repo.
    The gate combines three signals before deciding:

    - ``cosine`` (always-on baseline)
    - ``identifier_match`` boost: +0.10 when a snake-cased prompt token
      appears in any indexed file path (one strong hit lifts a 0.05-cosine
      prompt over the 0.15 floor)
    - ``domain_overlap`` boost: +0.01 per match, capped at 10 matches
      (so 7 vocab matches add +0.07 — meaningful but not overwhelming)

    Returns ``(cosine, info)`` where *info* contains diagnostic keys:
    ``cosine`` (raw), ``adjusted_score`` (cosine + boosts), ``decision``
    (``"pass"``/``"skip"``), ``reason`` (``"above_floor"``,
    ``"identifier_match"``, ``"overlap_boost"``, or ``"below_floor"``),
    plus ``domain_overlap`` + ``domain_matches`` + ``domain_vocab_size`` +
    ``identifier_match`` + ``identifier_hits`` for full observability.

    Used by :func:`ContextEnrichmentService.enrich` to gate codebase context
    injection, preventing unrelated projects from inheriting the linked
    repo's internal patterns.
    """
    import numpy as np

    from app.services.pipeline_constants import REPO_RELEVANCE_FLOOR

    # Repo-identity prefix makes the synthesis embedding carry the project
    # identity alongside its tech-stack signature.  Without this, two
    # FastAPI+SQLAlchemy projects with similar architecture synthesis can
    # collide on cosine even when their domain focus is very different.
    anchor = explore_synthesis
    if repo_full_name:
        anchor = f"Project: {repo_full_name}\n{explore_synthesis}"

    # Component-level signal: stride-sample paths to cap the anchor at ~100
    # lines.  Alphabetical ordering would over-represent top-level
    # directories (e.g. take all `backend/...` and miss `frontend/...`) —
    # stride sampling walks the tree breadth-first in path-sorted order so
    # every major subtree contributes a roughly proportional share.
    if file_paths:
        if len(file_paths) > _MAX_ANCHOR_PATHS:
            stride = len(file_paths) / _MAX_ANCHOR_PATHS
            sampled = [file_paths[int(i * stride)] for i in range(_MAX_ANCHOR_PATHS)]
        else:
            sampled = list(file_paths)
        anchor = f"{anchor}\n\nComponents:\n" + "\n".join(sampled)

    prompt_vec = await embedding_service.aembed_single(raw_prompt)
    synth_vec = await embedding_service.aembed_single(anchor)
    cosine = float(
        np.dot(prompt_vec, synth_vec)
        / (np.linalg.norm(prompt_vec) * np.linalg.norm(synth_vec) + 1e-9)
    )

    # Diagnostic + additive signal: vocabulary overlap.  Domain vocab uses
    # *all* supplied paths (not the stride sample) — it's token-deduped
    # downstream so size is bounded by the real vocabulary.
    domain_vocab = extract_domain_vocab(explore_synthesis, file_paths=file_paths)
    prompt_lower = raw_prompt.lower()
    matches = [w for w in domain_vocab if w in prompt_lower]
    overlap_boost = _DOMAIN_OVERLAP_BOOST_PER_MATCH * min(
        len(matches), _DOMAIN_OVERLAP_CAP,
    )

    # Identifier-match signal: did the prompt name a class/module that
    # exists in the indexed file paths?  This is the strongest independent
    # signal — overrides a low cosine when the prompt is unambiguously
    # about a specific file in the repo.
    identifier_match, identifier_hits = _check_identifier_match(
        raw_prompt, file_paths,
    )
    identifier_boost = _IDENTIFIER_MATCH_BOOST if identifier_match else 0.0

    adjusted = cosine + identifier_boost + overlap_boost

    # Decision precedence — surface the strongest passing signal in ``reason``
    # so observability shows *why* the prompt was kept.
    if adjusted >= REPO_RELEVANCE_FLOOR:
        if cosine >= REPO_RELEVANCE_FLOOR:
            reason = "above_floor"
        elif identifier_match:
            reason = "identifier_match"
        else:
            reason = "overlap_boost"
        return cosine, {
            "cosine": round(cosine, 4),
            "adjusted_score": round(adjusted, 4),
            "domain_overlap": len(matches),
            "domain_matches": sorted(matches)[:10],
            "domain_vocab_size": len(domain_vocab),
            "identifier_match": identifier_match,
            "identifier_hits": identifier_hits[:5],
            "decision": "pass",
            "reason": reason,
        }

    return cosine, {
        "cosine": round(cosine, 4),
        "adjusted_score": round(adjusted, 4),
        "domain_overlap": len(matches),
        "domain_matches": sorted(matches)[:10],
        "domain_vocab_size": len(domain_vocab),
        "identifier_match": identifier_match,
        "identifier_hits": identifier_hits[:5],
        "decision": "skip",
        "reason": "below_floor",
    }


__all__ = ["extract_domain_vocab", "compute_repo_relevance"]
