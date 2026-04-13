"""Classification agreement tracker — compares heuristic vs LLM classifications.

Module-level singleton (matches existing get_injection_stats() pattern).
Records agreement/disagreement after every LLM analysis phase and exposes
rates via get_classification_agreement().rates() for the health endpoint.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ClassificationAgreement:
    """Rolling counters for heuristic-vs-LLM classification agreement."""

    total: int = 0
    task_type_agree: int = 0
    domain_agree: int = 0
    both_agree: int = 0
    strategy_intel_total: int = 0
    strategy_intel_hits: int = 0

    def record(
        self,
        heuristic_task_type: str,
        heuristic_domain: str,
        llm_task_type: str,
        llm_domain: str,
        *,
        prompt_snippet: str = "",
    ) -> None:
        """Record one comparison between heuristic and LLM classifications."""
        self.total += 1
        tt_match = heuristic_task_type == llm_task_type
        d_match = heuristic_domain == llm_domain
        if tt_match:
            self.task_type_agree += 1
        if d_match:
            self.domain_agree += 1
        if tt_match and d_match:
            self.both_agree += 1
        if not (tt_match and d_match):
            logger.info(
                "classification_disagreement: heuristic=%s+%s llm=%s+%s prompt=%.80s",
                heuristic_task_type, heuristic_domain,
                llm_task_type, llm_domain,
                prompt_snippet,
            )

    def record_strategy_intel(self, *, had_intel: bool) -> None:
        """Record whether strategy intelligence was non-null for this request."""
        self.strategy_intel_total += 1
        if had_intel:
            self.strategy_intel_hits += 1

    def rates(self) -> dict[str, object]:
        """Compute agreement rates for the health endpoint."""
        t = max(self.total, 1)
        si_t = max(self.strategy_intel_total, 1)
        return {
            "total": self.total,
            "task_type_agreement_rate": round(self.task_type_agree / t, 2),
            "domain_agreement_rate": round(self.domain_agree / t, 2),
            "both_agreement_rate": round(self.both_agree / t, 2),
            "strategy_intelligence_hit_rate": round(self.strategy_intel_hits / si_t, 2),
        }


_agreement = ClassificationAgreement()


def get_classification_agreement() -> ClassificationAgreement:
    """Return the module-level singleton."""
    return _agreement


def _reset_agreement() -> None:
    """Test-only: reset counters."""
    global _agreement  # noqa: PLW0603
    _agreement = ClassificationAgreement()
