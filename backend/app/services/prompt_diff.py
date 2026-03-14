"""Prompt diff utilities: hashing, divergence, cycle detection, dimension deltas.

Used by RetryOracle and pipeline diagnostics.
"""

import hashlib
import re
from dataclasses import dataclass

SCORE_DIMENSIONS = (
    "clarity_score",
    "specificity_score",
    "structure_score",
    "faithfulness_score",
    "conciseness_score",
)

SOFT_CYCLE_THRESHOLD = 0.10


@dataclass
class CycleResult:
    """Result of cycle detection."""

    type: str  # "hard" | "soft"
    matched_attempt: int  # 1-indexed attempt that matched (hard) or 0 (soft)
    divergence: float = 0.0  # only meaningful for soft cycles


def compute_prompt_hash(prompt: str) -> str:
    """Normalized hash for cycle detection. Case-insensitive, whitespace-collapsed."""
    normalized = re.sub(r"\s+", " ", prompt.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def compute_dimension_deltas(
    before: dict[str, int | float],
    after: dict[str, int | float],
) -> dict[str, int | float]:
    """Compute per-dimension score changes between two validation results."""
    deltas: dict[str, int | float] = {}
    for dim in SCORE_DIMENSIONS:
        b = before.get(dim)
        a = after.get(dim)
        if b is not None and a is not None:
            deltas[dim] = a - b
    return deltas


def extract_structure(text: str) -> dict[str, int]:
    """Extract structural features from a prompt for divergence computation.

    Returns counts of lines, paragraphs, list items, and code blocks.
    """
    if not text:
        return {"lines": 0, "paragraphs": 0, "lists": 0, "code_blocks": 0}

    lines = text.split("\n")
    line_count = len(lines)

    # Paragraphs: groups separated by blank lines
    paragraphs = len(
        [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    )

    # List items: lines starting with -, *, or number.
    list_items = sum(
        1 for line in lines if re.match(r"^\s*(?:[-*]|\d+\.)\s", line)
    )

    # Code blocks: fenced with ```
    code_blocks = len(re.findall(r"```", text)) // 2

    return {
        "lines": line_count,
        "paragraphs": paragraphs,
        "lists": list_items,
        "code_blocks": code_blocks,
    }


def _normalized_struct_distance(
    struct_a: dict[str, int], struct_b: dict[str, int]
) -> float:
    """Compute normalized distance between two structural feature dicts.

    Returns 0.0 (identical structure) to 1.0 (maximally different).
    """
    keys = set(struct_a) | set(struct_b)
    if not keys:
        return 0.0

    total_diff = 0.0
    total_max = 0.0
    for k in keys:
        a = struct_a.get(k, 0)
        b = struct_b.get(k, 0)
        total_diff += abs(a - b)
        total_max += max(a, b, 1)  # avoid division by zero

    return total_diff / total_max if total_max > 0 else 0.0


def compute_prompt_divergence(prompt_a: str, prompt_b: str) -> float:
    """Multi-signal divergence between two prompts.

    Combines:
    - Jaccard distance on sentence-level tokens (content signal)
    - Normalized structural distance (structure signal)

    Returns 0.0 (identical) to 1.0 (completely different).
    Higher = more exploration between attempts.
    """
    # Handle edge cases
    if not prompt_a and not prompt_b:
        return 0.0
    if not prompt_a or not prompt_b:
        return 1.0

    # --- Content signal: Jaccard distance on two granularities ---
    def _sentences(text: str) -> set[str]:
        parts = re.split(r"[.!?\n]+", text.strip().lower())
        return {re.sub(r"\s+", " ", s.strip()) for s in parts if s.strip()}

    def _words(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 0.0
        union = a | b
        if not union:
            return 0.0
        return 1.0 - len(a & b) / len(union)

    # Blend sentence-level and word-level Jaccard for robustness:
    # sentence-level captures structural/semantic changes,
    # word-level captures fine-grained token overlap.
    sent_dist = _jaccard(_sentences(prompt_a), _sentences(prompt_b))
    word_dist = _jaccard(_words(prompt_a), _words(prompt_b))
    content_dist = 0.5 * sent_dist + 0.5 * word_dist

    # --- Structure signal: normalized structural distance ---
    struct_a = extract_structure(prompt_a)
    struct_b = extract_structure(prompt_b)
    struct_dist = _normalized_struct_distance(struct_a, struct_b)

    # Weighted combination: content is primary, structure is secondary
    content_weight = 0.7
    structure_weight = 0.3
    raw = content_weight * content_dist + structure_weight * struct_dist

    return round(max(0.0, min(1.0, raw)), 4)


def detect_cycle(
    current_hash: str,
    previous_hashes: list[str],
    *,
    current_divergence: float | None = None,
    dimension_deltas: dict[str, float] | None = None,
) -> CycleResult | None:
    """Check for hard cycles (exact hash match) and soft cycles (low divergence).

    Hard cycle: current prompt hash matches a previous attempt exactly.
    Soft cycle: divergence below SOFT_CYCLE_THRESHOLD with negligible dimension
                movement, indicating the optimizer is stuck in a local minimum.

    Returns CycleResult if a cycle is detected, None otherwise.
    """
    # Hard cycle: exact hash match
    for i, h in enumerate(previous_hashes):
        if h == current_hash:
            return CycleResult(
                type="hard",
                matched_attempt=i + 1,
                divergence=0.0,
            )

    # Soft cycle: compound condition
    if current_divergence is not None and current_divergence < SOFT_CYCLE_THRESHOLD:
        # Check if dimension deltas are also negligible
        if dimension_deltas:
            max_delta = max(abs(v) for v in dimension_deltas.values())
            if max_delta >= 1.0:
                # Significant dimension movement — not a cycle
                return None

        return CycleResult(
            type="soft",
            matched_attempt=0,
            divergence=current_divergence,
        )

    return None
