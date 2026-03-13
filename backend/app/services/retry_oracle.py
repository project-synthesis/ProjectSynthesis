"""7-gate adaptive RetryOracle.

Replaces the fixed LOW_SCORE_THRESHOLD retry logic with a stateful oracle
that tracks five real-time signals across attempts within a single pipeline run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.services.prompt_diff import (
    SCORE_DIMENSIONS,
    compute_dimension_deltas,
    compute_prompt_entropy,
    compute_prompt_hash,
    detect_cycle,
)

logger = logging.getLogger(__name__)

# ── Configurable constants ───────────────────────────────────────────
ENTROPY_EXHAUSTION_THRESHOLD = 0.15
ENTROPY_EXPLORATION_THRESHOLD = 0.40
REGRESSION_RATIO_THRESHOLD = 0.40
ELASTICITY_HIGH = 0.60
ELASTICITY_LOW = 0.30
FOCUS_EFFECTIVENESS_LOW = 0.30
MOMENTUM_NEGATIVE_THRESHOLD = -0.30
MOMENTUM_DECAY_FACTOR = 0.70
DIMINISHING_RETURNS_BASE = 0.50
DIMINISHING_RETURNS_GROWTH = 1.30
THRESHOLD_LOWER_BOUND = 3.0
THRESHOLD_UPPER_BOUND = 8.0
DEFAULT_THRESHOLD = 5.0


@dataclass
class RetryDecision:
    """Result from should_retry()."""

    action: str  # "accept" | "accept_best" | "retry"
    reason: str
    focus_areas: list[str] = field(default_factory=list)
    best_attempt: int | None = None  # 0-indexed


@dataclass
class _Attempt:
    """Internal record of one pipeline attempt."""

    scores: dict
    overall_score: float
    prompt_hash: str
    focus_areas: list[str]
    dimension_deltas: dict[str, float] = field(default_factory=dict)


class RetryOracle:
    """Stateful retry decision engine.

    Instantiated once per pipeline run. Records attempts and decides
    whether to retry based on 7 gates.
    """

    def __init__(
        self,
        max_retries: int = 1,
        threshold: float = DEFAULT_THRESHOLD,
        user_weights: dict[str, float] | None = None,
        task_baseline: float | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.threshold = max(THRESHOLD_LOWER_BOUND, min(THRESHOLD_UPPER_BOUND, threshold))
        self.user_weights = user_weights
        self.task_baseline = task_baseline
        self._attempts: list[_Attempt] = []
        self._elasticity: dict[str, list[bool]] = {d: [] for d in SCORE_DIMENSIONS}
        self._focus_history: list[list[str]] = []
        self._last_entropy: float = 1.0
        self._last_prompt: str = ""

    @property
    def attempt_count(self) -> int:
        return len(self._attempts)

    @property
    def best_attempt_index(self) -> int:
        """Index of the attempt with the highest weighted overall score."""
        if not self._attempts:
            return 0
        if self.user_weights:
            scores = []
            for a in self._attempts:
                weighted = sum(
                    a.scores.get(d, 0) * self.user_weights.get(d, 0)
                    for d in SCORE_DIMENSIONS
                )
                total_w = sum(self.user_weights.get(d, 0) for d in SCORE_DIMENSIONS)
                scores.append(weighted / total_w if total_w > 0 else a.overall_score)
            return max(range(len(scores)), key=lambda i: scores[i])
        return max(range(len(self._attempts)), key=lambda i: self._attempts[i].overall_score)

    def record_attempt(
        self,
        scores: dict,
        prompt: str,
        focus_areas: list[str],
    ) -> None:
        """Record the results of an optimization attempt."""
        prompt_hash = compute_prompt_hash(prompt)
        overall = scores.get("overall_score", 0.0)
        if not isinstance(overall, (int, float)):
            overall = 0.0

        deltas: dict[str, float] = {}
        if self._attempts:
            prev = self._attempts[-1]
            deltas = compute_dimension_deltas(prev.scores, scores)
            for dim in SCORE_DIMENSIONS:
                if dim in (focus_areas or []):
                    improved = deltas.get(dim, 0) > 0
                    self._elasticity[dim].append(improved)

        if self._last_prompt:
            self._last_entropy = compute_prompt_entropy(self._last_prompt, prompt)
        self._last_prompt = prompt

        self._attempts.append(_Attempt(
            scores=scores,
            overall_score=float(overall),
            prompt_hash=prompt_hash,
            focus_areas=focus_areas,
            dimension_deltas=deltas,
        ))
        self._focus_history.append(focus_areas or [])

    def _compute_momentum(self) -> float:
        """Exponentially weighted moving delta of overall scores."""
        if len(self._attempts) < 2:
            return 0.0
        deltas = []
        for i in range(1, len(self._attempts)):
            deltas.append(self._attempts[i].overall_score - self._attempts[i - 1].overall_score)
        if not deltas:
            return 0.0
        weighted_sum = 0.0
        weight_total = 0.0
        for i, d in enumerate(reversed(deltas)):
            w = MOMENTUM_DECAY_FACTOR ** i
            weighted_sum += d * w
            weight_total += w
        return weighted_sum / weight_total if weight_total > 0 else 0.0

    def _compute_entropy(self) -> float:
        """Prompt entropy between last two attempts."""
        if len(self._attempts) < 2:
            return 1.0
        return self._last_entropy

    def _compute_regression_ratio(self) -> float:
        """Fraction of dimensions that degraded on the last attempt."""
        if len(self._attempts) < 2:
            return 0.0
        deltas = self._attempts[-1].dimension_deltas
        if not deltas:
            return 0.0
        degraded = sum(1 for v in deltas.values() if v < 0)
        return degraded / len(deltas)

    def _compute_focus_effectiveness(self) -> float:
        """Fraction of focused dimensions that improved."""
        if len(self._attempts) < 2:
            return 1.0
        last = self._attempts[-1]
        focus = last.focus_areas
        if not focus:
            return 1.0
        improved = sum(1 for d in focus if last.dimension_deltas.get(d, 0) > 0)
        return improved / len(focus)

    def _get_elasticity(self, dim: str) -> float:
        """Ratio of successful improvements when targeted for a dimension."""
        history = self._elasticity.get(dim, [])
        if not history:
            return 0.5
        return sum(history) / len(history)

    def should_retry(self) -> RetryDecision:
        """7-gate decision algorithm."""
        if not self._attempts:
            return RetryDecision(action="retry", reason="No attempts yet")

        latest = self._attempts[-1]

        # Gate 1: Score >= adapted threshold → ACCEPT
        if latest.overall_score >= self.threshold:
            return RetryDecision(
                action="accept",
                reason=f"Score {latest.overall_score} >= threshold {self.threshold}",
            )

        # Gate 2: Budget exhausted → ACCEPT_BEST
        if self.attempt_count > self.max_retries:
            return RetryDecision(
                action="accept_best",
                reason=f"Budget exhausted ({self.attempt_count} attempts, max {self.max_retries + 1})",
                best_attempt=self.best_attempt_index,
            )

        # Gate 3: Cycle detected → ACCEPT_BEST
        if self.attempt_count >= 2:
            previous_hashes = [a.prompt_hash for a in self._attempts[:-1]]
            cycle_match = detect_cycle(latest.prompt_hash, previous_hashes)
            if cycle_match is not None:
                return RetryDecision(
                    action="accept_best",
                    reason=f"Cycle detected: attempt {self.attempt_count} matches attempt {cycle_match}",
                    best_attempt=self.best_attempt_index,
                )

        # Gate 4: Creative exhaustion → ACCEPT_BEST
        entropy = self._last_entropy if self.attempt_count >= 2 else 1.0
        if entropy < ENTROPY_EXHAUSTION_THRESHOLD and self.attempt_count >= 2:
            return RetryDecision(
                action="accept_best",
                reason=f"Creative exhaustion: entropy {entropy:.3f} < {ENTROPY_EXHAUSTION_THRESHOLD}",
                best_attempt=self.best_attempt_index,
            )

        # Gate 5: Negative momentum → ACCEPT_BEST
        momentum = self._compute_momentum()
        if momentum < MOMENTUM_NEGATIVE_THRESHOLD and self.attempt_count >= 2:
            return RetryDecision(
                action="accept_best",
                reason=f"Negative momentum: {momentum:.3f} < {MOMENTUM_NEGATIVE_THRESHOLD}",
                best_attempt=self.best_attempt_index,
            )

        # Gate 6: Zero-sum trap → ACCEPT_BEST
        if self.attempt_count >= 3:
            r1 = self._attempts[-1].dimension_deltas
            r2 = self._attempts[-2].dimension_deltas
            if r1 and r2:
                ratio_1 = sum(1 for v in r1.values() if v < 0) / max(len(r1), 1)
                ratio_2 = sum(1 for v in r2.values() if v < 0) / max(len(r2), 1)
                if ratio_1 > REGRESSION_RATIO_THRESHOLD and ratio_2 > REGRESSION_RATIO_THRESHOLD:
                    return RetryDecision(
                        action="accept_best",
                        reason=f"Zero-sum trap: regression ratio {ratio_1:.2f}, {ratio_2:.2f}",
                        best_attempt=self.best_attempt_index,
                    )

        # Gate 7: Diminishing returns → ACCEPT_BEST
        min_expected_gain = DIMINISHING_RETURNS_BASE * (DIMINISHING_RETURNS_GROWTH ** (self.attempt_count - 1))
        if momentum < min_expected_gain and self.attempt_count >= 2:
            return RetryDecision(
                action="accept_best",
                reason=f"Diminishing returns: momentum {momentum:.3f} < expected {min_expected_gain:.3f}",
                best_attempt=self.best_attempt_index,
            )

        # All gates passed → RETRY
        focus = self._select_focus()
        return RetryDecision(action="retry", reason="All gates passed", focus_areas=focus)

    def _select_focus(self) -> list[str]:
        """Select dimensions to focus the next retry on."""
        if not self._attempts:
            return []

        if self.attempt_count >= 3:
            eff_1 = self._compute_focus_effectiveness()
            if eff_1 < FOCUS_EFFECTIVENESS_LOW:
                logger.info(
                    "Focus effectiveness %.2f < %.2f, going unconstrained",
                    eff_1,
                    FOCUS_EFFECTIVENESS_LOW,
                )
                return []

        latest = self._attempts[-1]
        dim_scores = []
        for dim in SCORE_DIMENSIONS:
            score = latest.scores.get(dim)
            if score is not None:
                elasticity = self._get_elasticity(dim)
                if elasticity >= ELASTICITY_LOW:
                    dim_scores.append((dim, score, elasticity))

        dim_scores.sort(key=lambda x: x[1])
        return [d[0] for d in dim_scores[:2]]

    def build_diagnostic_message(self, focus_areas: list[str]) -> str:
        """Build a diagnostic message for the optimizer as a refinement turn."""
        if not self._attempts:
            return "Improve the prompt."

        latest = self._attempts[-1]
        parts = ["The previous optimization attempt scored:"]

        for dim in SCORE_DIMENSIONS:
            score = latest.scores.get(dim)
            if score is not None:
                delta = latest.dimension_deltas.get(dim)
                delta_str = f" ({'+' if delta and delta > 0 else ''}{delta})" if delta else ""
                parts.append(f"  - {dim.replace('_score', '')}: {score}/10{delta_str}")

        if focus_areas:
            focus_names = [f.replace("_score", "") for f in focus_areas]
            parts.append(f"\nFocus on improving: {', '.join(focus_names)}.")

        momentum = self._compute_momentum()
        if momentum < 0.2:
            parts.append("Try a structurally different approach rather than incremental refinement.")

        return "\n".join(parts)

    def get_diagnostics(self) -> dict:
        """Return diagnostic data for SSE events.

        The returned dict is consumed by the frontend RetryDiagnostics component.
        Keys must match the component's TypeScript interface exactly.
        """
        latest = self._attempts[-1] if self._attempts else None
        decision = self.should_retry() if self.attempt_count > 0 else None

        # Determine which gate fired (from decision reason or "pending")
        gate = "pending"
        if decision:
            reason_lower = decision.reason.lower()
            if "threshold" in reason_lower:
                gate = "threshold"
            elif "budget" in reason_lower:
                gate = "budget"
            elif "cycle" in reason_lower:
                gate = "cycle"
            elif "exhaustion" in reason_lower or "entropy" in reason_lower:
                gate = "entropy"
            elif "diminishing" in reason_lower:
                gate = "diminishing"
            elif "momentum" in reason_lower:
                gate = "momentum"
            elif "regression" in reason_lower:
                gate = "regression"

        return {
            "attempt": self.attempt_count,
            "overall_score": round(latest.overall_score, 3) if latest else 0,
            "threshold": round(self.threshold, 3),
            "action": decision.action if decision else "pending",
            "reason": decision.reason if decision else "",
            "focus_areas": decision.focus_areas if decision else [],
            "gate": gate,
            "momentum": round(self._compute_momentum(), 3),
            "best_attempt_index": self.best_attempt_index,
            "best_score": self._attempts[self.best_attempt_index].overall_score if self._attempts else None,
        }
