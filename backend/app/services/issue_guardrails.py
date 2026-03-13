"""Issue guardrail and verification prompt builders."""
from __future__ import annotations

from app.config import settings
from app.services.framework_profiles import ISSUE_GUARDRAILS


def _merge_issue_counts(
    user_freq: dict[str, int],
    framework_freq: dict[str, int] | None,
) -> dict[str, int]:
    merged = dict(user_freq)
    if framework_freq:
        for issue_id, count in framework_freq.items():
            merged[issue_id] = merged.get(issue_id, 0) + count
    return merged


def build_issue_guardrails(
    issue_frequency: dict[str, int],
    framework_issue_freq: dict[str, int] | None,
) -> str:
    """Generate optimizer prompt guardrails from issue history.
    Returns empty string if no guardrails needed."""
    merged = _merge_issue_counts(issue_frequency, framework_issue_freq)
    threshold = settings.MIN_ISSUE_FREQUENCY_FOR_GUARDRAIL

    guardrails = []
    for issue_id, count in sorted(merged.items(), key=lambda x: -x[1]):
        if count >= threshold and issue_id in ISSUE_GUARDRAILS:
            guardrails.append(ISSUE_GUARDRAILS[issue_id])

    if not guardrails:
        return ""

    capped = guardrails[:settings.MAX_ISSUE_GUARDRAILS]
    return (
        "\n\n## Quality Guardrails (from user feedback history)\n"
        + "\n".join(f"- {g}" for g in capped)
    )


def build_issue_verification_prompt(
    issue_frequency: dict[str, int],
) -> str | None:
    """Build targeted verification checks for the validator prompt.
    Returns None if no issues warrant extra checking."""
    threshold = settings.MIN_ISSUE_FREQUENCY_FOR_GUARDRAIL
    checks = []

    if issue_frequency.get("lost_key_terms", 0) >= threshold:
        checks.append(
            "TERM CHECK: Extract key technical terms from the original. "
            "Verify each appears in the optimized version (or a precise synonym)."
        )
    if issue_frequency.get("changed_meaning", 0) >= threshold:
        checks.append(
            "INTENT CHECK: Summarize what the original prompt asks an LLM to do. "
            "Verify the optimized version asks for the same thing."
        )
    if issue_frequency.get("hallucinated_content", 0) >= threshold:
        checks.append(
            "ADDITION CHECK: Identify any requirements, constraints, or examples "
            "in the optimized version not present in the original. Flag them."
        )
    if issue_frequency.get("too_verbose", 0) >= threshold:
        checks.append(
            "CONCISENESS CHECK: Count sentences that add no new information. "
            "Flag redundancy."
        )

    if not checks:
        return None

    return (
        "\n\n## Issue Verification (user-reported patterns)\n"
        + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(checks))
        + "\nDeduct from faithfulness_score for each failed check."
    )
