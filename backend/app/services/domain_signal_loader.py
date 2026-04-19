"""DomainSignalLoader — dynamic heuristic keyword signals from domain metadata.

Loads keyword signals from PromptCluster nodes that have ``state="domain"``.
Each domain node stores its signals in ``cluster_metadata["signal_keywords"]``
as a list of ``[keyword, weight]`` pairs.

This replaces the hardcoded ``_DOMAIN_SIGNALS`` dict in ``heuristic_analyzer.py``
so the set of recognized domains and their weights evolve with the taxonomy.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-level singleton — mirrors DomainResolver pattern
# ---------------------------------------------------------------------------

_instance: DomainSignalLoader | None = None


def get_signal_loader() -> DomainSignalLoader | None:
    """Return the process-level DomainSignalLoader, or None if not initialized."""
    return _instance


def set_signal_loader(loader: DomainSignalLoader | None) -> None:
    """Set the process-level DomainSignalLoader (called by lifespan)."""
    global _instance
    _instance = loader


class DomainSignalLoader:
    """Loads domain keyword signals from taxonomy domain nodes.

    Usage::

        loader = DomainSignalLoader()
        await loader.load(db)          # populate from DB
        scored = loader.score(words)   # {domain: weight_sum}
        domain = loader.classify(scored)  # "backend" | "backend: security" | "general"
    """

    def __init__(self) -> None:
        self._signals: dict[str, list[tuple[str, float]]] = {}
        self._patterns: dict[str, re.Pattern[str]] = {}
        # Organic qualifier vocabulary cache — populated by Phase 5 via
        # refresh_qualifiers() and by load() from domain node metadata.
        self._qualifier_cache: dict[str, dict[str, list[str]]] = {}
        self._qualifier_hits: int = 0
        self._qualifier_misses: int = 0
        self._last_qualifier_refresh: str | None = None
        # Qualifier embedding cache — keyed by sorted keyword string.
        # Eliminates repeated MiniLM embedding calls for the same keyword set.
        self._qualifier_embedding_cache: dict[str, Any] = {}
        self._qualifier_embeddings_generated: int = 0
        self._qualifier_embeddings_skipped: int = 0

    # ------------------------------------------------------------------
    # Properties (return copies to protect internal state)
    # ------------------------------------------------------------------

    @property
    def signals(self) -> dict[str, list[tuple[str, float]]]:
        """Mapping of domain → list of (keyword, weight) tuples."""
        return dict(self._signals)

    @property
    def patterns(self) -> dict[str, re.Pattern[str]]:
        """Pre-compiled word-boundary regex patterns keyed by keyword."""
        return dict(self._patterns)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    async def load(self, db: AsyncSession) -> None:
        """Query domain nodes and populate signal tables.

        Silently ignores DB errors so a taxonomy failure never crashes the
        heuristic path.
        """
        try:
            rows = await db.execute(
                select(PromptCluster).where(PromptCluster.state == "domain")
            )
            clusters = rows.scalars().all()

            new_signals: dict[str, list[tuple[str, float]]] = {}
            new_qualifier_cache: dict[str, dict[str, list[str]]] = {}
            for cluster in clusters:
                keywords = self._extract_keywords(cluster.cluster_metadata)
                if keywords:
                    new_signals[cluster.label] = keywords

                # Also load organic qualifier vocabulary from metadata
                gen_qual = self._extract_generated_qualifiers(cluster.cluster_metadata)
                if gen_qual:
                    new_qualifier_cache[cluster.label] = gen_qual

            self._signals = new_signals
            self._precompile_patterns()
            self._qualifier_cache = new_qualifier_cache
            if new_qualifier_cache:
                logger.info(
                    "DomainSignalLoader loaded qualifier vocab for %d domains",
                    len(new_qualifier_cache),
                )

            total_keywords = sum(len(kws) for kws in new_signals.values())
            logger.info(
                "DomainSignalLoader loaded %d domains with %d total keywords",
                len(self._signals), total_keywords,
            )
            # Only warn when there are *actual* non-general domain nodes with
            # no signals — the seed 'general' domain legitimately has no
            # signal_keywords because it's the fallback classification, and
            # shipping that warning at every startup with only seed data was
            # misleading noise.
            non_general_clusters = [c for c in clusters if c.label != "general"]
            if not self._signals and non_general_clusters:
                logger.warning(
                    "DomainSignalLoader: %d non-general domain node(s) present "
                    "but no signal_keywords extracted — classifier will default "
                    "to 'general' for them",
                    len(non_general_clusters),
                )
        except Exception:
            logger.exception("DomainSignalLoader.load failed — using empty signals")

    def _extract_keywords(
        self, metadata: Any
    ) -> list[tuple[str, float]]:
        """Extract ``signal_keywords`` from cluster_metadata.

        Returns an empty list when the key is absent or the value is malformed.
        """
        if not isinstance(metadata, dict):
            return []
        raw = metadata.get("signal_keywords")
        if not raw or not isinstance(raw, list):
            return []

        pairs: list[tuple[str, float]] = []
        for item in raw:
            try:
                keyword, weight = item[0], float(item[1])
                if isinstance(keyword, str) and keyword:
                    pairs.append((keyword, weight))
            except (IndexError, TypeError, ValueError):
                continue
        return pairs

    def _extract_generated_qualifiers(
        self, metadata: Any,
    ) -> dict[str, list[str]]:
        """Extract ``generated_qualifiers`` from cluster_metadata.

        Returns an empty dict when the key is absent or the value is malformed.
        """
        if not isinstance(metadata, dict):
            return {}
        raw = metadata.get("generated_qualifiers")
        if not raw or not isinstance(raw, dict):
            return {}
        # Validate structure: {str: list[str]}
        result: dict[str, list[str]] = {}
        for key, val in raw.items():
            if isinstance(key, str) and isinstance(val, list):
                keywords = [v for v in val if isinstance(v, str)]
                if keywords:
                    result[key] = keywords
        return result

    def _precompile_patterns(self) -> None:
        """Compile ``\\b<keyword>\\b`` regex for every single-word keyword."""
        patterns: dict[str, re.Pattern[str]] = {}
        for keywords in self._signals.values():
            for keyword, _weight in keywords:
                kw = keyword.lower()
                if " " not in kw and kw not in patterns:
                    patterns[kw] = re.compile(r"\b" + re.escape(kw) + r"\b")
        self._patterns = patterns

    # ------------------------------------------------------------------
    # Runtime signal registration (A3 auto-enrichment)
    # ------------------------------------------------------------------

    def register_signals(
        self, domain: str, keywords: list[tuple[str, float]],
    ) -> None:
        """Register keyword signals for a domain at runtime.

        Called by the domain signal extractor after the warm path discovers
        new domains. Immediately available for subsequent heuristic calls.
        Safe under asyncio cooperative scheduling (no await between read/write).
        """
        if not keywords:
            return
        self._signals[domain] = keywords
        self._precompile_patterns()
        logger.info(
            "register_signals: domain=%s keywords=%d (e.g. %s)",
            domain, len(keywords),
            ", ".join(kw for kw, _ in keywords[:3]),
        )
        # Taxonomy observability — Activity Panel
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="warm",
                op="signal_enrichment",
                decision="signals_registered",
                context={
                    "domain": domain,
                    "keyword_count": len(keywords),
                    "keywords": [kw for kw, _ in keywords[:5]],
                    "total_domains_with_signals": len(self._signals),
                },
            )
        except RuntimeError:
            pass  # Event logger not initialized

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def score(self, words: set[str]) -> dict[str, float]:
        """Sum keyword weights for each domain given a set of words.

        ``words`` should be a set of lowercase tokens from the prompt text.
        Multi-word keywords are skipped (they require full-text matching
        outside this method).
        """
        scored: dict[str, float] = {}
        for domain, keywords in self._signals.items():
            total = 0.0
            for keyword, weight in keywords:
                kw = keyword.lower()
                if " " not in kw and kw in words:
                    total += weight
            if total > 0:
                scored[domain] = total
        return scored

    def classify(self, scored: dict[str, float]) -> str:
        """Return the most-likely domain label given pre-computed domain scores.

        Rules:

        - Empty or no-signal signals dict → ``"general"``
        - No domain scores at all → ``"general"``
        - Both ``backend`` and ``frontend`` ≥ 1.5 → ``"fullstack"``
        - Top domain score < 1.0 → ``"general"``
        - Secondary domain also ≥ 1.0 → ``"primary: secondary"``
        - Otherwise → primary domain label

        When ``_signals`` is empty (no domain nodes loaded), always returns
        ``"general"`` regardless of the ``scored`` values.
        """
        if not self._signals:
            return "general"
        if not scored:
            return "general"

        # Fullstack promotion: both backend AND frontend score significantly
        if scored.get("backend", 0) >= 1.5 and scored.get("frontend", 0) >= 1.5:
            return "fullstack"

        sorted_domains = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        if not sorted_domains or sorted_domains[0][1] < 1.0:
            return "general"

        primary = sorted_domains[0][0]

        if len(sorted_domains) >= 2:
            secondary, secondary_score = sorted_domains[1]
            if secondary_score >= 1.0 and secondary != primary:
                return f"{primary}: {secondary}"

        return primary

    # ------------------------------------------------------------------
    # Qualifier vocabulary cache (organic sub-domain discovery)
    # ------------------------------------------------------------------

    def get_qualifiers(self, domain: str) -> dict[str, list[str]]:
        """Return the organic qualifier vocabulary for a domain.

        Returns an empty dict on cache miss (domain has no vocabulary yet).
        Never raises.
        """
        result = self._qualifier_cache.get(domain.strip().lower(), {})
        if result:
            self._qualifier_hits += 1
        else:
            self._qualifier_misses += 1
        return result

    def refresh_qualifiers(
        self, domain: str, qualifiers: dict[str, list[str]],
    ) -> None:
        """Push freshly generated qualifier vocabulary into the cache.

        Called by Phase 5 after Haiku generates vocabulary for a domain.
        Immediately available for subsequent hot-path enrichment calls.
        """
        from datetime import datetime, timezone

        if not qualifiers:
            return
        self._qualifier_cache[domain.strip().lower()] = qualifiers
        self.invalidate_qualifier_embedding_cache()
        self._last_qualifier_refresh = datetime.now(timezone.utc).isoformat()
        logger.info(
            "refresh_qualifiers: domain=%s groups=%d (e.g. %s)",
            domain, len(qualifiers),
            ", ".join(list(qualifiers.keys())[:3]),
        )

    # ------------------------------------------------------------------
    # Qualifier embedding cache (eliminates repeated MiniLM calls)
    # ------------------------------------------------------------------

    def cache_qualifier_embedding(self, key: str, embedding: Any) -> None:
        """Cache a qualifier embedding keyed by sorted keyword string."""
        self._qualifier_embedding_cache[key] = embedding

    def get_cached_qualifier_embedding(self, key: str) -> Any:
        """Look up a cached qualifier embedding. Returns None on miss."""
        return self._qualifier_embedding_cache.get(key)

    def invalidate_qualifier_embedding_cache(self) -> None:
        """Clear all cached qualifier embeddings (called on vocab refresh)."""
        self._qualifier_embedding_cache.clear()

    def remove_domain(self, label: str) -> None:
        """Remove all cached data for a domain (called on domain dissolution).

        Clears keyword signals, compiled patterns, organic qualifier vocabulary,
        and qualifier embedding cache for the specified domain. Safe to call
        with a label that doesn't exist.
        """
        lbl = label.strip().lower()
        removed_signals = len(self._signals.pop(lbl, []))
        self._qualifier_cache.pop(lbl, None)
        # Rebuild patterns without the removed domain's keywords
        self._precompile_patterns()
        # Clear embedding cache (may contain embeddings referencing removed keywords)
        self.invalidate_qualifier_embedding_cache()
        logger.info(
            "remove_domain: domain=%s signals_removed=%d",
            lbl, removed_signals,
        )

    @staticmethod
    def find_best_qualifier(
        text: str, qualifiers: dict[str, list[str]],
    ) -> tuple[str | None, int]:
        """Find the qualifier with the most keyword hits in the given text.

        Shared utility used by both hot-path enrichment
        (``_enrich_domain_qualifier``) and warm-path Phase 5 Source 2
        (intent_label matching in ``_propose_sub_domains``).

        Args:
            text: Lowercased text to scan for keywords.
            qualifiers: Mapping of qualifier name → keyword list.

        Returns:
            Tuple of (best_qualifier_name, hit_count). Returns (None, 0) if
            no qualifier has any keyword hits.
        """
        best: str | None = None
        best_hits = 0
        for name, keywords in qualifiers.items():
            hits = sum(1 for kw in keywords if kw in text)
            if hits > best_hits:
                best_hits = hits
                best = name
        return best, best_hits

    def stats(self) -> dict:
        """Return diagnostic stats for the health endpoint."""
        return {
            "qualifier_cache_hits": self._qualifier_hits,
            "qualifier_cache_misses": self._qualifier_misses,
            "domains_with_vocab": len(self._qualifier_cache),
            "domains_without_vocab": 0,  # not tracked globally
            "last_qualifier_refresh": self._last_qualifier_refresh,
            "qualifier_embeddings_generated": self._qualifier_embeddings_generated,
            "qualifier_embeddings_skipped": self._qualifier_embeddings_skipped,
            "qualifier_embedding_cache_size": len(self._qualifier_embedding_cache),
        }
