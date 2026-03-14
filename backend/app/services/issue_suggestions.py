"""Proactive issue suggestion engine — suggests likely issues based on scores + history."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.framework_profiles import SCORE_ISSUE_MAP


@dataclass
class SuggestedIssue:
    issue_id: str
    reason: str
    confidence: float  # 0.0 to 1.0


def suggest_likely_issues(
    scores: dict[str, float],
    framework: str,
    framework_issue_freq: dict[str, int] | None,
    user_issue_freq: dict[str, int] | None,
) -> list[SuggestedIssue]:
    """Analyze scores and history to suggest likely issues. Returns top 3."""
    suggestions: list[SuggestedIssue] = []

    # Signal 1: Low scores suggest specific issues
    for dim, issues in SCORE_ISSUE_MAP.items():
        score = scores.get(dim, 10.0)
        if score < 6.0:
            for issue_id in issues:
                suggestions.append(SuggestedIssue(
                    issue_id=issue_id,
                    reason=f"scored {score:.1f}/10 on {dim.replace('_score', '')}",
                    confidence=min(0.9, (6.0 - score) / 4.0),
                ))

    # Signal 2: Framework history
    if framework_issue_freq:
        for issue_id, count in framework_issue_freq.items():
            if count >= 2:
                suggestions.append(SuggestedIssue(
                    issue_id=issue_id,
                    reason=f"reported {count}x previously with {framework}",
                    confidence=min(0.85, count * 0.2),
                ))

    # Signal 3: User-global patterns
    if user_issue_freq:
        for issue_id, count in user_issue_freq.items():
            if count >= 3:
                suggestions.append(SuggestedIssue(
                    issue_id=issue_id,
                    reason=f"reported {count}x across optimizations",
                    confidence=min(0.8, count * 0.15),
                ))

    # Deduplicate: keep highest confidence per issue_id
    best: dict[str, SuggestedIssue] = {}
    for s in suggestions:
        if s.issue_id not in best or s.confidence > best[s.issue_id].confidence:
            best[s.issue_id] = s

    return sorted(best.values(), key=lambda s: -s.confidence)[:3]
