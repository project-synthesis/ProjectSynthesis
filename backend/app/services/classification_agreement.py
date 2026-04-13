"""Classification agreement tracker — compares heuristic vs LLM classifications.

Module-level singleton (matches existing get_injection_stats() pattern).
Records agreement/disagreement after every LLM analysis phase and exposes
rates via get_classification_agreement().rates() for the health endpoint.

E1b: When ``_cross_process`` is True (set in MCP lifespan), ``record()``
and ``record_strategy_intel()`` fire-and-forget HTTP POST to the backend
process so the health endpoint aggregates data from both processes.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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

    # E1b: When True, forward records to backend process via HTTP POST.
    # Set by MCP lifespan init. Backend singleton keeps this False to
    # prevent infinite forwarding loops.
    _cross_process: bool = field(default=False, repr=False)

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
        if self._cross_process:
            self._forward("classification_agreement_record", {
                "heuristic_task_type": heuristic_task_type,
                "heuristic_domain": heuristic_domain,
                "llm_task_type": llm_task_type,
                "llm_domain": llm_domain,
                "prompt_snippet": prompt_snippet,
            })

    def record_strategy_intel(self, *, had_intel: bool) -> None:
        """Record whether strategy intelligence was non-null for this request."""
        self.strategy_intel_total += 1
        if had_intel:
            self.strategy_intel_hits += 1
        if self._cross_process:
            self._forward("classification_agreement_strategy_intel", {
                "had_intel": had_intel,
            })

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

    # ------------------------------------------------------------------
    # E1b: Cross-process forwarding (MCP → backend)
    # ------------------------------------------------------------------

    def _forward(self, event_type: str, data: dict) -> None:
        """Fire-and-forget HTTP POST to backend via event notification."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self._forward_async(event_type, data),
                name=f"ca_fwd_{event_type.split('_')[-1]}",
            )
            # prevent GC of unawaited task
            task.add_done_callback(lambda t: None)
        except RuntimeError:
            # No event loop — use sync fallback (thread)
            self._forward_sync(event_type, data)
        except Exception as exc:
            logger.warning("classification_agreement forward setup failed: %s", exc)

    @staticmethod
    async def _forward_async(event_type: str, data: dict) -> None:
        """Async forwarding via the shared event notification module."""
        try:
            from app.services.event_notification import notify_event_bus

            await notify_event_bus(event_type, data)
        except Exception as exc:
            logger.debug("classification_agreement async forward failed: %s", exc)

    @staticmethod
    def _forward_sync(event_type: str, data: dict) -> None:
        """Sync fallback when no event loop is available."""
        import threading

        def _post() -> None:
            try:
                import httpx

                httpx.post(
                    "http://127.0.0.1:8000/api/events/_publish",
                    json={"event_type": event_type, "data": data},
                    timeout=3.0,
                )
            except Exception as exc:
                logger.debug("classification_agreement sync forward failed: %s", exc)

        threading.Thread(target=_post, daemon=True).start()


_agreement = ClassificationAgreement()


def get_classification_agreement() -> ClassificationAgreement:
    """Return the module-level singleton."""
    return _agreement


def _reset_agreement() -> None:
    """Test-only: reset counters."""
    global _agreement  # noqa: PLW0603
    _agreement = ClassificationAgreement()
