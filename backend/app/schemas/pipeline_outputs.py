"""Pydantic models for pipeline stage structured outputs.

These models serve as the single source of truth for JSON Schema generation
(via ``model_json_schema()``) and client-side validation.  All models use
``extra="forbid"`` which produces ``additionalProperties: false`` in the
generated schema — required by the Anthropic structured-output API.

LLM-generated classification fields (``intent_category``, ``task_type``,
``complexity``) use ``str`` rather than ``Literal`` to avoid rejecting novel
values and crashing the entire response.  Score fields use ``Field(ge=1, le=10)``
for server-side range enforcement.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ── Explore stage (Stage 0) ──────────────────────────────────────────────


class IntentClassificationOutput(BaseModel):
    """Output of the pre-explore intent classifier."""

    model_config = ConfigDict(extra="forbid")

    intent_category: str
    observation_directives: list[str]
    snippet_priorities: list[str]
    depth: str


class CodeSnippet(BaseModel):
    """A single code snippet extracted during codebase exploration."""

    model_config = ConfigDict(extra="forbid")

    file: str
    lines: str = ""
    context: str


class ExploreSynthesisOutput(BaseModel):
    """Output of the single-shot explore synthesis LLM call."""

    model_config = ConfigDict(extra="forbid")

    tech_stack: list[str]
    key_files_read: list[str]
    relevant_code_snippets: list[CodeSnippet] = []
    codebase_observations: list[str]
    prompt_grounding_notes: list[str]
    coverage_pct: int | None = None


# ── Analyze stage (Stage 1) ──────────────────────────────────────────────


class AnalyzeOutput(BaseModel):
    """Output of the prompt analysis / classification stage."""

    model_config = ConfigDict(extra="forbid")

    task_type: str = "general"
    complexity: str = "moderate"
    weaknesses: list[str] = []
    strengths: list[str] = []
    recommended_frameworks: list[str] = []


# ── Strategy stage (Stage 2) ─────────────────────────────────────────────


class StrategyOutput(BaseModel):
    """Output of the framework selection stage."""

    model_config = ConfigDict(extra="forbid")

    primary_framework: str
    secondary_frameworks: list[str] = []
    rationale: str = ""
    approach_notes: str = ""


# ── Optimize stage (Stage 3) — fallback only ─────────────────────────────


class OptimizeFallbackOutput(BaseModel):
    """Structured output for the optimizer's complete_json fallback path.

    The primary optimize path uses streaming + ``<optimization_meta>`` markers.
    This model is only used when the streaming path fails and the fallback
    ``complete_parsed()`` call is needed.
    """

    model_config = ConfigDict(extra="forbid")

    optimized_prompt: str
    changes_made: list[str] = []
    framework_applied: str = ""
    optimization_notes: str = ""


# ── Validate stage (Stage 4) ─────────────────────────────────────────────


class ValidateOutput(BaseModel):
    """Output of the scoring / validation stage.

    Score fields enforce ``ge=1, le=10`` — the Anthropic API enforces the
    range server-side when using ``messages.parse()``, and Pydantic validates
    client-side on the streaming-parse path.
    """

    model_config = ConfigDict(extra="forbid")

    clarity_score: int = Field(ge=1, le=10, default=5)
    specificity_score: int = Field(ge=1, le=10, default=5)
    structure_score: int = Field(ge=1, le=10, default=5)
    faithfulness_score: int = Field(ge=1, le=10, default=5)
    conciseness_score: int = Field(ge=1, le=10, default=5)
    is_improvement: bool = False
    verdict: str = ""
    issues: list[str] = []
