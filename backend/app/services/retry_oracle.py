"""7-gate adaptive RetryOracle.

Replaces the fixed LOW_SCORE_THRESHOLD retry logic with a stateful oracle
that tracks five real-time signals across attempts within a single pipeline run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.services.framework_profiles import DEFAULT_FRAMEWORK_PROFILE, FRAMEWORK_PROFILES
from app.services.prompt_diff import (
    SCORE_DIMENSIONS,
    compute_dimension_deltas,
    compute_prompt_divergence,
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


class GateName(str, Enum):
    """Identifies which gate fired in a RetryDecision."""

    THRESHOLD_MET = "threshold_met"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CYCLE_DETECTED = "cycle_detected"
    CREATIVE_EXHAUSTION = "creative_exhaustion"
    NEGATIVE_MOMENTUM = "negative_momentum"
    ZERO_SUM_TRAP = "zero_sum_trap"
    DIMINISHING_RETURNS = "diminishing_returns"
    FRAMEWORK_MISMATCH = "framework_mismatch"  # Gate 0 advisory


@dataclass
class RetryDecision:
    """Result from should_retry()."""

    action: str  # "accept" | "accept_best" | "retry"
    reason: str
    focus_areas: list[str] = field(default_factory=list)
    best_attempt: int | None = None  # 0-indexed
    gate: GateName | None = None


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
        framework: str | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.threshold = max(
            THRESHOLD_LOWER_BOUND, min(THRESHOLD_UPPER_BOUND, threshold)
        )
        self.user_weights = user_weights
        self._framework = framework
        self._attempts: list[_Attempt] = []
        self._elasticity: dict[str, list[bool]] = {d: [] for d in SCORE_DIMENSIONS}
        self._elasticity_matrix: dict[str, dict[str, float]] = {}
        self._focus_history: list[list[str]] = []
        self._last_divergence: float = 1.0
        self._last_prompt: str = ""
        self._last_decision: RetryDecision | None = None

    @property
    def framework(self) -> str | None:
        return self._framework

    @framework.setter
    def framework(self, value: str | None) -> None:
        self._framework = value

    @property
    def attempts(self) -> list:
        return list(self._attempts)

    @property
    def last_decision(self) -> RetryDecision | None:
        return self._last_decision

    def get_elasticity_snapshot(self) -> dict[str, dict[str, float]]:
        """Return a copy of the elasticity matrix for persistence."""
        return {fw: dict(dims) for fw, dims in self._elasticity_matrix.items()}

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
                total_w = sum(
                    self.user_weights.get(d, 0) for d in SCORE_DIMENSIONS
                )
                scores.append(
                    weighted / total_w if total_w > 0 else a.overall_score
                )
            return max(range(len(scores)), key=lambda i: scores[i])
        return max(
            range(len(self._attempts)),
            key=lambda i: self._attempts[i].overall_score,
        )

    def record_attempt(
        self,
        scores: dict,
        prompt: str,
        focus_areas: list[str],
    ) -> None:
        """Record the results of an optimization attempt."""
        prompt_hash = compute_prompt_hash(prompt)
        overall = scores.get("overall_score")
        if overall is None or not isinstance(overall, (int, float)):
            # Compute from dimension scores when overall_score is absent
            dim_vals = [
                scores[d]
                for d in SCORE_DIMENSIONS
                if d in scores and isinstance(scores[d], (int, float))
            ]
            overall = sum(dim_vals) / len(dim_vals) if dim_vals else 0.0

        deltas: dict[str, float] = {}
        if self._attempts:
            prev = self._attempts[-1]
            deltas = compute_dimension_deltas(prev.scores, scores)
            # Legacy per-focus elasticity tracking
            for dim in SCORE_DIMENSIONS:
                if dim in (focus_areas or []):
                    improved = deltas.get(dim, 0) > 0
                    self._elasticity[dim].append(improved)

            # Update elasticity matrix for ALL dimensions
            self._update_elasticity_matrix(deltas)

        if self._last_prompt:
            self._last_divergence = compute_prompt_divergence(
                self._last_prompt, prompt
            )
        self._last_prompt = prompt

        self._attempts.append(
            _Attempt(
                scores=scores,
                overall_score=float(overall),
                prompt_hash=prompt_hash,
                focus_areas=focus_areas,
                dimension_deltas=deltas,
            )
        )
        self._focus_history.append(focus_areas or [])

    def _update_elasticity_matrix(self, deltas: dict[str, float]) -> None:
        """Update per-framework, per-dimension elasticity using EMA.

        Tracks absolute delta magnitude as elasticity signal for ALL
        dimensions, not just focus areas. Uses exponential moving average
        so recent attempts weigh more heavily.
        """
        if not self._framework:
            return

        alpha = settings.ELASTICITY_EMA_ALPHA
        cold_start = settings.ELASTICITY_COLD_START

        if self._framework not in self._elasticity_matrix:
            self._elasticity_matrix[self._framework] = {}

        fw_entry = self._elasticity_matrix[self._framework]
        for dim in SCORE_DIMENSIONS:
            delta_val = abs(deltas.get(dim, 0.0))
            prev = fw_entry.get(dim, cold_start)
            fw_entry[dim] = alpha * delta_val + (1 - alpha) * prev

    def get_elasticity(
        self, framework: str, dimension: str
    ) -> float:
        """Return elasticity value for a framework+dimension pair.

        Defaults to ELASTICITY_COLD_START if no data exists.
        """
        cold_start = settings.ELASTICITY_COLD_START
        fw_entry = self._elasticity_matrix.get(framework, {})
        return fw_entry.get(dimension, cold_start)

    def _compute_momentum(self) -> float:
        """Exponentially weighted moving delta of overall scores."""
        if len(self._attempts) < 2:
            return 0.0
        deltas = []
        for i in range(1, len(self._attempts)):
            deltas.append(
                self._attempts[i].overall_score
                - self._attempts[i - 1].overall_score
            )
        if not deltas:
            return 0.0
        weighted_sum = 0.0
        weight_total = 0.0
        for i, d in enumerate(reversed(deltas)):
            w = MOMENTUM_DECAY_FACTOR**i
            weighted_sum += d * w
            weight_total += w
        return weighted_sum / weight_total if weight_total > 0 else 0.0

    def _compute_divergence(self) -> float:
        """Prompt divergence between last two attempts."""
        if len(self._attempts) < 2:
            return 1.0
        return self._last_divergence

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

    def _get_framework_emphasis(self, dim: str) -> float:
        """Return the framework emphasis multiplier for a dimension.

        Returns the emphasis value if the dimension is emphasized by the
        framework profile, or the de_emphasis value if it is de-emphasized.
        Defaults to 1.0 (neutral) for dimensions not listed in either.
        """
        if not self._framework:
            return 1.0
        profile = FRAMEWORK_PROFILES.get(
            self._framework, DEFAULT_FRAMEWORK_PROFILE
        )
        emphasis = profile.get("emphasis", {})
        de_emphasis = profile.get("de_emphasis", {})
        if dim in emphasis:
            return emphasis[dim]
        if dim in de_emphasis:
            return de_emphasis[dim]
        return 1.0

    def should_retry(self) -> RetryDecision:
        """7-gate decision algorithm."""
        if not self._attempts:
            decision = RetryDecision(action="retry", reason="No attempts yet")
            self._last_decision = decision
            return decision

        latest = self._attempts[-1]

        # Gate 1: Score >= adapted threshold -> ACCEPT
        if latest.overall_score >= self.threshold:
            decision = RetryDecision(
                action="accept",
                reason=(
                    f"Score {latest.overall_score} >= "
                    f"threshold {self.threshold}"
                ),
                gate=GateName.THRESHOLD_MET,
            )
            self._last_decision = decision
            return decision

        # Gate 2: Budget exhausted -> ACCEPT_BEST (>= fix: off-by-one)
        if self.attempt_count >= self.max_retries + 1:
            decision = RetryDecision(
                action="accept_best",
                reason=(
                    f"Budget exhausted ({self.attempt_count} attempts, "
                    f"max {self.max_retries + 1})"
                ),
                best_attempt=self.best_attempt_index,
                gate=GateName.BUDGET_EXHAUSTED,
            )
            self._last_decision = decision
            return decision

        # Gate 3: Cycle detected -> ACCEPT_BEST
        if self.attempt_count >= 2:
            previous_hashes = [a.prompt_hash for a in self._attempts[:-1]]
            cycle_result = detect_cycle(
                latest.prompt_hash,
                previous_hashes,
                current_divergence=self._last_divergence,
                dimension_deltas=latest.dimension_deltas,
            )
            if cycle_result is not None:
                if cycle_result.type == "hard":
                    decision = RetryDecision(
                        action="accept_best",
                        reason=(
                            f"Cycle detected: attempt {self.attempt_count} "
                            f"matches attempt {cycle_result.matched_attempt}"
                        ),
                        best_attempt=self.best_attempt_index,
                        gate=GateName.CYCLE_DETECTED,
                    )
                else:
                    decision = RetryDecision(
                        action="accept_best",
                        reason=(
                            f"Soft cycle detected: divergence "
                            f"{cycle_result.divergence:.3f} below threshold"
                        ),
                        best_attempt=self.best_attempt_index,
                        gate=GateName.CYCLE_DETECTED,
                    )
                self._last_decision = decision
                return decision

        # Gate 4: Creative exhaustion -> ACCEPT_BEST
        divergence = self._last_divergence if self.attempt_count >= 2 else 1.0
        if (
            divergence < ENTROPY_EXHAUSTION_THRESHOLD
            and self.attempt_count >= 2
        ):
            decision = RetryDecision(
                action="accept_best",
                reason=(
                    f"Creative exhaustion: divergence {divergence:.3f} "
                    f"< {ENTROPY_EXHAUSTION_THRESHOLD}"
                ),
                best_attempt=self.best_attempt_index,
                gate=GateName.CREATIVE_EXHAUSTION,
            )
            self._last_decision = decision
            return decision

        # Gate 5: Negative momentum -> ACCEPT_BEST
        momentum = self._compute_momentum()
        if momentum < MOMENTUM_NEGATIVE_THRESHOLD and self.attempt_count >= 2:
            decision = RetryDecision(
                action="accept_best",
                reason=(
                    f"Negative momentum: {momentum:.3f} "
                    f"< {MOMENTUM_NEGATIVE_THRESHOLD}"
                ),
                best_attempt=self.best_attempt_index,
                gate=GateName.NEGATIVE_MOMENTUM,
            )
            self._last_decision = decision
            return decision

        # Gate 6: Zero-sum trap -> ACCEPT_BEST
        if self.attempt_count >= 3:
            r1 = self._attempts[-1].dimension_deltas
            r2 = self._attempts[-2].dimension_deltas
            if r1 and r2:
                ratio_1 = sum(1 for v in r1.values() if v < 0) / max(
                    len(r1), 1
                )
                ratio_2 = sum(1 for v in r2.values() if v < 0) / max(
                    len(r2), 1
                )
                if (
                    ratio_1 > REGRESSION_RATIO_THRESHOLD
                    and ratio_2 > REGRESSION_RATIO_THRESHOLD
                ):
                    decision = RetryDecision(
                        action="accept_best",
                        reason=(
                            f"Zero-sum trap: regression ratio "
                            f"{ratio_1:.2f}, {ratio_2:.2f}"
                        ),
                        best_attempt=self.best_attempt_index,
                        gate=GateName.ZERO_SUM_TRAP,
                    )
                    self._last_decision = decision
                    return decision

        # Gate 7: Diminishing returns -> ACCEPT_BEST
        min_expected_gain = DIMINISHING_RETURNS_BASE * (
            DIMINISHING_RETURNS_GROWTH ** (self.attempt_count - 1)
        )
        if momentum < min_expected_gain and self.attempt_count >= 2:
            decision = RetryDecision(
                action="accept_best",
                reason=(
                    f"Diminishing returns: momentum {momentum:.3f} "
                    f"< expected {min_expected_gain:.3f}"
                ),
                best_attempt=self.best_attempt_index,
                gate=GateName.DIMINISHING_RETURNS,
            )
            self._last_decision = decision
            return decision

        # All gates passed -> RETRY
        focus = self._select_focus_areas()
        decision = RetryDecision(
            action="retry", reason="All gates passed", focus_areas=focus
        )
        self._last_decision = decision
        return decision

    def _select_focus_areas(self) -> list[str]:
        """Select dimensions to focus the next retry on.

        When a framework is set, uses framework profile emphasis to weight
        focus selection: expected_improvement = score_gap x elasticity x
        framework_emphasis. Dimensions the framework de-emphasizes
        (emphasis < 1.0) are excluded.
        """
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

        if self._framework:
            # Framework-aware focus selection
            dim_candidates = []
            for dim in SCORE_DIMENSIONS:
                score = latest.scores.get(dim)
                if score is None:
                    continue
                fw_emphasis = self._get_framework_emphasis(dim)
                if fw_emphasis < 1.0:
                    # Framework de-emphasizes this dimension — skip
                    continue
                score_gap = self.threshold - score
                if score_gap <= 0:
                    continue
                elasticity = self.get_elasticity(self._framework, dim)
                expected_improvement = score_gap * elasticity * fw_emphasis
                dim_candidates.append((dim, expected_improvement))

            # Sort by expected improvement descending, pick top 2
            dim_candidates.sort(key=lambda x: x[1], reverse=True)
            return [d[0] for d in dim_candidates[:2]]

        # Fallback: original logic (no framework)
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
                delta_str = (
                    f" ({'+' if delta and delta > 0 else ''}{delta})"
                    if delta
                    else ""
                )
                parts.append(
                    f"  - {dim.replace('_score', '')}: {score}/10{delta_str}"
                )

        if focus_areas:
            focus_names = [f.replace("_score", "") for f in focus_areas]
            parts.append(
                f"\nFocus on improving: {', '.join(focus_names)}."
            )

        momentum = self._compute_momentum()
        if momentum < 0.2:
            parts.append(
                "Try a structurally different approach "
                "rather than incremental refinement."
            )

        return "\n".join(parts)

    def get_diagnostics(self) -> dict:
        """Return diagnostic data for SSE events.

        The returned dict is consumed by the frontend RetryDiagnostics component.
        Keys must match the component's TypeScript interface exactly.
        """
        latest = self._attempts[-1] if self._attempts else None
        decision = (
            self._last_decision
            if self._last_decision
            else (self.should_retry() if self.attempt_count > 0 else None)
        )

        # Use gate enum from decision
        gate = "pending"
        if decision and decision.gate is not None:
            gate = decision.gate.value
        elif decision:
            # Fallback for decisions without gate (should not happen)
            reason_lower = decision.reason.lower()
            if "threshold" in reason_lower:
                gate = "threshold_met"
            elif "budget" in reason_lower:
                gate = "budget_exhausted"
            elif "cycle" in reason_lower:
                gate = "cycle_detected"
            elif "exhaustion" in reason_lower or "entropy" in reason_lower:
                gate = "creative_exhaustion"
            elif "diminishing" in reason_lower:
                gate = "diminishing_returns"
            elif "momentum" in reason_lower:
                gate = "negative_momentum"
            elif "regression" in reason_lower:
                gate = "zero_sum_trap"

        return {
            "attempt": self.attempt_count,
            "overall_score": (
                round(latest.overall_score, 3) if latest else 0
            ),
            "threshold": round(self.threshold, 3),
            "action": decision.action if decision else "pending",
            "reason": decision.reason if decision else "",
            "focus_areas": decision.focus_areas if decision else [],
            "gate": gate,
            "momentum": round(self._compute_momentum(), 3),
            "best_attempt_index": self.best_attempt_index,
            "best_score": (
                self._attempts[self.best_attempt_index].overall_score
                if self._attempts
                else None
            ),
        }
