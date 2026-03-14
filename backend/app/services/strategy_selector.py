"""Heuristic fallback for strategy selection.

Maps task_type to a default framework when the LLM strategy call fails.
Provides framework approach templates for strategy overrides and heuristic fallback.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Framework approach templates ────────────────────────────────────────────
# Each template produces ~150-250 words of substantive approach notes when
# interpolated with {complexity_guidance} and {weaknesses_summary}.
# Used by build_override_approach_notes() for strategy overrides and
# heuristic_strategy_fallback() when the LLM strategy stage fails.

_COMPLEXITY_DEPTH: dict[str, str] = {
    "simple": "concise, focused coverage — minimal scaffolding, 1-3 targeted sections",
    "moderate": "thorough coverage — full framework application with all relevant sections populated",
    "complex": "exhaustive, multi-layered coverage — comprehensive constraint sets and deeply structured sections",
}

FRAMEWORK_APPROACH_TEMPLATES: dict[str, str] = {
    "chain-of-thought": (
        "Apply chain-of-thought by decomposing the prompt into explicit reasoning stages. "
        "Each stage should build on the previous one's output, making intermediate logic visible. "
        "Structure the reasoning chain so the model must show its work before reaching conclusions. "
        "Include verification checkpoints where the model validates its intermediate results.\n\n"
        "The chain should progress from problem decomposition through evidence gathering to synthesis. "
        "Each stage should name its input dependency and define its expected output format. "
        "For multi-faceted problems, consider parallel reasoning branches that converge at a synthesis step. "
        "Avoid chains longer than 5-7 stages — beyond that, group related reasoning into composite stages. "
        "Flag assumptions at each stage explicitly. When the chain encounters ambiguity, split into "
        "conditional branches rather than guessing a single path.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through the reasoning structure: {weaknesses_summary}. "
        "Frame each weakness as a reasoning gap that the chain-of-thought stages must explicitly close. "
        "Ensure the final stage synthesizes all intermediate findings into a coherent conclusion."
    ),
    "constraint-injection": (
        "Apply constraint-injection by identifying implicit requirements in the prompt and making them "
        "explicit boundary conditions. Define hard constraints (must/must-not) separately from soft "
        "preferences (should/ideally). Order constraints by priority so the model resolves conflicts "
        "predictably — higher-priority constraints override lower ones.\n\n"
        "Group constraints by category: scope constraints (what to include/exclude), quality constraints "
        "(standards the output must meet), format constraints (structural requirements), and behavioral "
        "constraints (what the model should and should not do). For each constraint, state it as a "
        "testable assertion — if you cannot verify compliance, the constraint is too vague. "
        "Use constraint hierarchies: non-negotiable > strong preference > nice-to-have. "
        "Assign each constraint a verification method so the executor can confirm compliance.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through explicit constraints: {weaknesses_summary}. "
        "Each weakness should map to one or more concrete constraints that prevent the failure mode. "
        "Include validation criteria so the output can be checked against the constraint set."
    ),
    "context-enrichment": (
        "Apply context-enrichment by augmenting the prompt with domain background, definitions, and "
        "reference information that the model needs to produce an informed response. Structure the "
        "context hierarchically: essential background first, then domain-specific details, then "
        "edge cases and exceptions.\n\n"
        "Separate context into distinct labeled blocks: domain definitions, relevant prior work or "
        "standards, environmental constraints, and assumed knowledge. Each block should be clearly "
        "delimited so the model knows which information is background versus instruction. Avoid "
        "injecting context that duplicates what the model already knows — focus on domain-specific or "
        "situational details the model cannot infer.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through targeted context additions: {weaknesses_summary}. "
        "For each weakness, determine what missing context would prevent the failure and inject it. "
        "Ensure context blocks are clearly labeled so the model distinguishes background from instructions."
    ),
    "CO-STAR": (
        "Apply the CO-STAR framework by structuring the prompt into Context (situation and background), "
        "Objective (what to achieve), Style (writing style and approach), Tone (emotional register), "
        "Audience (who will read the output), and Response format (structure and length). "
        "Each section must contain substantive content — empty structural headers are worse than omission. "
        "If the prompt's scope only warrants 3-4 CO-STAR sections, omit the others rather than padding.\n\n"
        "Context should establish the situational frame that shapes interpretation. Objective must be "
        "specific and measurable — avoid vague goals. Style and Tone should be distinct: Style governs "
        "structure and vocabulary level, Tone governs emotional register. Audience determines the assumed "
        "knowledge baseline. Response format should specify structure, length, and any constraints.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through the CO-STAR structure: {weaknesses_summary}. "
        "Map each weakness to the CO-STAR section that resolves it — missing audience awareness maps to "
        "Audience, vague scope maps to Objective, inconsistent voice maps to Style/Tone."
    ),
    "few-shot-scaffolding": (
        "Apply few-shot scaffolding by providing concrete input-output examples that demonstrate the "
        "expected behavior. Examples should cover the typical case, an edge case, and a boundary case. "
        "Order examples from simplest to most nuanced so the model builds pattern recognition "
        "progressively.\n\n"
        "Each example should include the input, the expected output, and a brief annotation explaining "
        "why this output is correct. Use consistent formatting across examples so the pattern is "
        "unambiguous. If the task has multiple valid outputs, show the preferred one and note "
        "alternatives. Avoid examples that are so simple they teach nothing or so complex they obscure "
        "the core pattern. Include one adversarial example that demonstrates a common mistake and the "
        "correct response, so the model learns the boundary between right and wrong.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through example selection: {weaknesses_summary}. "
        "Design at least one example that specifically demonstrates correct handling of each identified "
        "weakness. Include brief annotations explaining why each example response is correct."
    ),
    "persona-assignment": (
        "Apply persona-assignment by defining a specific expert role with clear expertise boundaries, "
        "communication style, and domain knowledge. The persona should be concrete enough to constrain "
        "behavior (not just 'an expert') but flexible enough to handle the prompt's full scope. "
        "Include what the persona would and would NOT do.\n\n"
        "Define the persona's credentials, years of experience in the relevant domain, and their "
        "characteristic approach to problem-solving. Specify their communication register (technical "
        "vs accessible, formal vs conversational) and how they handle ambiguity — do they ask "
        "clarifying questions, state assumptions, or proceed with caveats? Define what the persona "
        "does NOT know or care about as sharply as what they do — negative constraints prevent the "
        "model from defaulting to omniscient behavior.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through persona design: {weaknesses_summary}. "
        "The persona's expertise should directly cover the areas where the original prompt is weakest. "
        "Define the persona's approach to uncertainty and edge cases explicitly."
    ),
    "RISEN": (
        "Apply the RISEN framework: Role (expert identity), Instructions (step-by-step task), "
        "Steps (ordered execution plan), End goal (success criteria), Narrowing (scope boundaries). "
        "Each section must be substantive — the optimizer relies on these as its primary structural "
        "signal for the rewrite.\n\n"
        "Role should specify domain expertise and communication style. Instructions should be "
        "action-oriented with clear verbs. Steps should form a logical sequence with each step "
        "building on the previous. End goal must define what success looks like in measurable terms. "
        "Narrowing must explicitly state what is out of scope and what failure modes to avoid.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through the RISEN structure: {weaknesses_summary}. "
        "Use the Narrowing section to explicitly exclude failure modes identified in the weaknesses. "
        "The End goal should define measurable success criteria that demonstrate the weaknesses are resolved."
    ),
    "role-task-format": (
        "Apply role-task-format by clearly separating WHO should respond (role), WHAT they should do "
        "(task), and HOW to structure the output (format). Keep the structure lightweight — this "
        "framework's strength is clarity without overhead. Avoid over-engineering with unnecessary "
        "scaffolding.\n\n"
        "The role should be specific enough to set expertise level and communication style. The task "
        "description should be unambiguous with clear success criteria. The format section should "
        "specify structure (sections, lists, paragraphs), length expectations, and any mandatory "
        "elements. This framework works best when the prompt's core issue is ambiguity, not complexity.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through structural clarity: {weaknesses_summary}. "
        "Use the task definition to close ambiguity gaps and the format definition to prevent "
        "output structure issues."
    ),
    "step-by-step": (
        "Apply step-by-step decomposition by breaking the prompt's objective into an ordered sequence "
        "of discrete steps. Each step should have a clear input, action, and expected output. "
        "Include dependency markers where later steps require earlier outputs. Define what constitutes "
        "completion for each step.\n\n"
        "Number steps explicitly and state prerequisites for each. Group related steps into phases "
        "when the sequence is long. Include decision points where the path branches based on "
        "intermediate results. For steps that might fail, include fallback instructions or error "
        "handling guidance.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through step design: {weaknesses_summary}. "
        "Insert explicit verification or validation steps after the steps most likely to surface "
        "the identified weaknesses."
    ),
    "structured-output": (
        "Apply structured-output by defining an explicit output schema with field names, types, and "
        "descriptions. Specify required vs optional fields, handling of missing data, and validation "
        "rules. Include a concrete example of the expected output shape.\n\n"
        "Define the schema format (JSON, YAML, table, or custom) and specify exact field names, types, "
        "and cardinality. For each field, describe what constitutes valid content and how to handle "
        "edge cases (nulls, empty arrays, unknown values). Include at least one complete example "
        "showing the expected output with realistic data.\n\n"
        "Depth calibration: {complexity_guidance}.\n\n"
        "Address these identified weaknesses through output structure: {weaknesses_summary}. "
        "Design the schema so that each weakness maps to a required field or validation rule that "
        "forces the model to address it. Include edge case handling in the field descriptions."
    ),
}

_GENERIC_APPROACH_TEMPLATE = (
    "Apply the {framework} framework to restructure this prompt. Focus on the framework's core "
    "principles to improve clarity, specificity, and actionability. Ensure every structural element "
    "contains substantive content — do not pad sections to fill the template.\n\n"
    "Identify which structural elements the framework provides and populate only the ones relevant "
    "to this prompt's scope. Each element should contain concrete, actionable content. If the "
    "framework defines sections that do not apply, omit them rather than inserting placeholder text. "
    "Prioritize the elements that most directly address the prompt's identified weaknesses.\n\n"
    "Depth calibration: {complexity_guidance}.\n\n"
    "Address these identified weaknesses: {weaknesses_summary}. "
    "Map each weakness to the framework element that most directly resolves it."
)


def build_override_approach_notes(framework: str, analysis: dict[str, Any]) -> str:
    """Build substantive approach notes for a strategy override or heuristic fallback.

    Interpolates the framework's approach template with complexity-based depth
    guidance and the top 3 weaknesses from analysis.

    Returns ~150-250 words of concrete optimizer guidance instead of a stub.
    """
    complexity = analysis.get("complexity", "moderate") if analysis else "moderate"
    complexity_guidance = _COMPLEXITY_DEPTH.get(complexity, _COMPLEXITY_DEPTH["moderate"])

    weaknesses = analysis.get("weaknesses", []) if analysis else []
    if weaknesses:
        top_weaknesses = weaknesses[:3]
        weaknesses_summary = "; ".join(str(w) for w in top_weaknesses)
    else:
        weaknesses_summary = "no specific weaknesses identified — focus on general clarity and structure"

    template = FRAMEWORK_APPROACH_TEMPLATES.get(framework, _GENERIC_APPROACH_TEMPLATE)

    return template.format(
        framework=framework,
        complexity_guidance=complexity_guidance,
        weaknesses_summary=weaknesses_summary,
    )


# Mapping of task_type -> (primary_framework, secondary_frameworks, rationale)
TASK_FRAMEWORK_MAP: dict[str, tuple[str, list[str], str]] = {
    "coding": (
        "structured-output",
        ["constraint-injection"],
        "Coding tasks benefit from strict output format specifications and explicit constraints "
        "to reduce ambiguity in generated code.",
    ),
    "analysis": (
        "chain-of-thought",
        ["context-enrichment"],
        "Analysis tasks require step-by-step reasoning. Chain-of-thought helps surface "
        "intermediate logic, while context enrichment ensures all relevant information is considered.",
    ),
    "reasoning": (
        "chain-of-thought",
        ["step-by-step"],
        "Reasoning tasks are best served by explicit chain-of-thought prompting "
        "combined with step-by-step decomposition.",
    ),
    "math": (
        "step-by-step",
        ["chain-of-thought"],
        "Mathematical tasks require explicit step-by-step decomposition to avoid arithmetic "
        "errors and ensure each step is verifiable.",
    ),
    "writing": (
        "CO-STAR",
        ["persona-assignment"],
        "Writing tasks benefit from the full CO-STAR framework (Context, Objective, Style, Tone, "
        "Audience, Response) combined with a clear persona.",
    ),
    "creative": (
        "persona-assignment",
        ["few-shot-scaffolding", "CO-STAR"],
        "Creative tasks benefit from a strong persona combined with structural guidance "
        "and examples to inspire while maintaining quality.",
    ),
    "extraction": (
        "structured-output",
        ["constraint-injection"],
        "Data extraction tasks need precise output format specifications and boundary constraints.",
    ),
    "classification": (
        "few-shot-scaffolding",
        ["structured-output"],
        "Classification tasks perform best with concrete examples showing expected categorization.",
    ),
    "formatting": (
        "structured-output",
        ["role-task-format"],
        "Formatting tasks need explicit output structure and a clear task definition.",
    ),
    "medical": (
        "RISEN",
        ["context-enrichment", "constraint-injection"],
        "Medical tasks require the RISEN framework for role clarity, careful context enrichment, "
        "and explicit safety constraints.",
    ),
    "legal": (
        "RISEN",
        ["context-enrichment", "constraint-injection"],
        "Legal tasks require precise role assignment, thorough context, and explicit constraints "
        "for jurisdictional and regulatory compliance.",
    ),
    "education": (
        "CO-STAR",
        ["step-by-step", "few-shot-scaffolding"],
        "Educational tasks benefit from clear audience-aware structuring (CO-STAR) combined with "
        "pedagogical step-by-step progression and examples.",
    ),
    "general": (
        "role-task-format",
        ["structured-output"],
        "General tasks benefit from clear role + task + format structure without imposing "
        "heavyweight framework scaffolding.",
    ),
    "other": (
        "role-task-format",
        [],
        "Default to lightweight role-task-format for unclassified tasks to avoid "
        "over-engineering with framework-heavy approaches.",
    ),
}


# All task_type values recognised by the heuristic map.
# If the analyzer ever returns a value outside this set, something new has
# been added — log a warning so developers can decide whether to add it.
KNOWN_TASK_TYPES: frozenset[str] = frozenset(TASK_FRAMEWORK_MAP.keys())

# All unique framework names referenced in TASK_FRAMEWORK_MAP (primary + secondary).
# Single source of truth — settings validation and frontend dropdowns derive from this.
KNOWN_FRAMEWORKS: frozenset[str] = frozenset(
    framework
    for primary, secondaries, _ in TASK_FRAMEWORK_MAP.values()
    for framework in [primary, *secondaries]
)


def build_affinity_prompt_section(
    task_type: str,
    strategy_affinities: dict | None,
) -> str:
    """Build a prompt section injecting user's framework preferences.

    Returns empty string if no affinities for this task type.
    """
    if not strategy_affinities:
        return ""
    affinities = strategy_affinities.get(task_type, {})
    if not affinities:
        return ""
    preferred = affinities.get("preferred", [])
    avoid = affinities.get("avoid", [])
    if not preferred and not avoid:
        return ""
    lines = ["\n## User Framework Preferences (from feedback history)"]
    if preferred:
        lines.append(f"- Preferred frameworks: {', '.join(preferred)}")
    if avoid:
        lines.append(f"- Frameworks to avoid: {', '.join(avoid)}")
    lines.append(
        "Weight these preferences when selecting a framework, but override "
        "if the prompt characteristics strongly favor a different choice."
    )
    return "\n".join(lines)


def heuristic_strategy_fallback(task_type: str) -> dict:
    """Return a heuristic strategy based on task type.

    Used as a fallback when the LLM strategy stage fails.
    Unknown task types fall back to 'general' with a warning so the
    gap is visible in logs rather than silently swallowed.
    """
    if task_type not in KNOWN_TASK_TYPES:
        logger.warning(
            "Unknown task_type %r from analyzer — using 'general' heuristic. "
            "Add %r to TASK_FRAMEWORK_MAP in strategy_selector.py if this is a valid category.",
            task_type,
            task_type,
        )
        task_type = "general"

    primary, secondary, rationale = TASK_FRAMEWORK_MAP.get(
        task_type,
        TASK_FRAMEWORK_MAP["general"],
    )

    approach_notes = build_override_approach_notes(primary, {"complexity": "moderate"})

    return {
        "primary_framework": primary,
        "secondary_frameworks": secondary,
        "rationale": f"[Heuristic fallback] {rationale}",
        "approach_notes": approach_notes,
    }
