"""Pure model-capability helpers.

Single source of truth for which effort levels / thinking modes each Claude
model supports, plus human-readable labels. Used by:

  - ``/api/settings`` to expose the model catalog to the frontend, so the
    Navigator can filter effort options per phase's selected model instead
    of showing a global list that 400s on Sonnet/Haiku.
  - ``claude_cli.py`` / ``anthropic_api.py`` for xhigh-effort gating and
    downgrade logic (replaces inline ``"opus-4-7" not in model_lower``
    string checks scattered across providers).

All helpers are pure (no I/O) and case-insensitive. Model IDs are the ones
used in ``config.py`` — the shape ``claude-{tier}-{major}-{minor}`` (e.g.
``claude-opus-4-7``). Short tier strings (``opus``/``sonnet``/``haiku``)
also resolve via ``model_tier()`` for preference-layer inputs.

Per Anthropic docs (2026-04):
  - Haiku: effort parameter has NO effect (CLI skips ``--effort`` entirely,
    API rejects it on 4.5). Thinking not supported.
  - Sonnet 4.5: low/medium/high. ``max`` returns 400.
  - Sonnet 4.6: low/medium/high/max. Adaptive thinking supported.
  - Opus 4.5 / 4.6: low/medium/high/max. Adaptive thinking supported.
  - Opus 4.7: low/medium/high/xhigh/max. Adaptive thinking supported;
    ``display: "summarized"`` required to surface thinking text in streams
    (defaults to ``omitted`` otherwise).
"""

from __future__ import annotations

_ALL_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_KNOWN_TIERS = ("opus", "sonnet", "haiku")


def effort_support(model: str) -> list[str]:
    """Return the ordered list of effort levels supported by ``model``.

    Empty list means the model does not accept any effort value — callers
    must not pass ``effort`` to the provider at all. (Conceptually distinct
    from "supports default effort" — Haiku simply ignores the parameter.)

    Ordering matches UI display order (lowest to highest intensity).
    """
    lower = model.lower()
    if "haiku" in lower:
        return []
    if "opus-4-7" in lower:
        return ["low", "medium", "high", "xhigh", "max"]
    if "opus-4-6" in lower or "opus-4-5" in lower:
        return ["low", "medium", "high", "max"]
    if "sonnet-4-6" in lower:
        return ["low", "medium", "high", "max"]
    if "sonnet-4-5" in lower:
        return ["low", "medium", "high"]
    # Unknown — conservative default (no max, no xhigh).
    return ["low", "medium", "high"]


def supports_thinking(model: str) -> bool:
    """Whether the model supports the ``thinking`` parameter.

    Haiku rejects ``thinking`` entirely. Opus / Sonnet 4.x support adaptive
    thinking (``{"type": "adaptive"}``). Opus 4.7 additionally accepts
    ``display: "summarized"`` to keep thinking text visible in streams.
    """
    return "haiku" not in model.lower()


def model_tier(model: str) -> str | None:
    """Extract tier name (``opus``/``sonnet``/``haiku``) from a model ID.

    Accepts both full IDs (``claude-opus-4-7``) and bare tier strings
    (``opus``). Returns ``None`` if no known tier is found.
    """
    lower = model.lower()
    for tier in _KNOWN_TIERS:
        if tier in lower:
            return tier
    return None


def tier_display_name(tier: str) -> str:
    """Capitalize a tier string for UI display (``opus`` → ``Opus``)."""
    return tier.capitalize()


def model_version(model: str) -> str:
    """Extract dotted version from a model ID (``claude-opus-4-7`` → ``4.7``).

    Returns empty string if the ID has no numeric version suffix.
    """
    parts = model.lower().replace("claude-", "").split("-")
    # Skip the tier name; collect trailing digit segments.
    numeric = [p for p in parts[1:] if p.isdigit()]
    return ".".join(numeric)


def model_label(model: str) -> str:
    """Human-readable display label (``claude-opus-4-7`` → ``Opus 4.7``).

    Falls back to title-casing the tier segment for unknown models.
    """
    parts = model.lower().replace("claude-", "").split("-")
    if not parts:
        return model
    tier = parts[0].capitalize()
    version = model_version(model)
    return f"{tier} {version}" if version else tier
