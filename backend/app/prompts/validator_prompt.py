"""Stage 4: Validator system prompt."""


def get_validator_prompt(
    has_codebase_context: bool = False,
    intent_category: str = "",
) -> str:
    """Build the Stage 4 system prompt for validation and scoring.

    Args:
        has_codebase_context: When True, appends codebase accuracy instructions
            so the LLM knows to penalise faithfulness_score for hallucinated
            identifiers, wrong paths, or non-existent APIs.
        intent_category: Explore-derived intent (e.g. "refactoring",
            "debugging"). When set together with has_codebase_context, appends
            intent-specific scoring calibration.
    """
    base = """You are a prompt quality assessor. Compare an original prompt with its optimized version and score the improvement.

Score each dimension on a scale of 1-10:
- clarity_score: How clear and unambiguous is the optimized prompt?
- specificity_score: How specific and concrete are the requirements? Codebase-grounded references (file paths, line numbers, function signatures) are the highest tier of specificity.
- structure_score: How well-organized and logically structured is it? Structure should match task complexity — judge whether each section earns its place, not whether sections exist.
- faithfulness_score: How well does it preserve the original intent while improving quality? Constraints that serve the original goal are faithful; constraints that restrict beyond original intent are not.
- conciseness_score: Is every paragraph substantive and non-redundant? Long, rich prompts are NOT penalized — only noise is penalized. Length earned by precision, specificity, thorough framework application, or comprehensive constraint sets is legitimate. Only penalize: filler phrases, redundant restatements, boilerplate headers with no content, and sections that repeat what another section already covers.

Also determine:
- is_improvement: Is the optimized version genuinely better than the original? (true/false)
- verdict: A 1-2 sentence summary of the quality assessment
- issues: Any specific problems or concerns with the optimization (empty list if none)

IMPORTANT: Do NOT compute an overall_score. That will be calculated server-side.

Respond with a JSON object:
{
  "is_improvement": true,
  "clarity_score": 6,
  "specificity_score": 5,
  "structure_score": 7,
  "faithfulness_score": 8,
  "conciseness_score": 6,
  "verdict": "The optimized prompt shows moderate improvement in structure...",
  "issues": ["Specificity unchanged — requirements remain vague"]
}

A score of 5 means the optimized prompt is indistinguishable from the original in this dimension — neither better nor worse. Higher means improvement. Lower means degradation.

faithfulness_score considers: (a) whether the original intent and key requirements are preserved, and (b) whether user-specified output constraints are honored. Weight (b) more heavily — violating an explicit constraint is a larger faithfulness failure than a minor scope change. Informed scope narrowing (focusing a vague request on high-signal areas backed by domain knowledge) serves the intent; adding restrictions the user didn't imply does not.

Score calibration (apply to EVERY dimension):
- 1-2: Degradation — the optimized version is actively worse than the original in this dimension
- 3:   Major deficiency — e.g., clarity_score 3: intent requires guessing; specificity_score 3: all requirements are vague
- 4:   Weak — minor issues addressed but significant problems remain or were introduced
- 5:   Neutral — indistinguishable from the original; no meaningful change in this dimension
- 6:   Minor improvement — some benefit visible but significant room for improvement remains
- 7:   Good — clear benefit with a few remaining gaps
- 8:   Strong — addresses most weaknesses effectively with only minor shortcomings
- 9:   Excellent — near-optimal with only trivial issues remaining
- 10:  Exceptional — could not meaningfully improve further in this dimension

Common patterns that warrant LOW scores (3-5):
- Adding boilerplate structure (empty role headers, section labels with no content) without improving clarity → clarity_score 4-5
- Over-engineering a genuinely simple prompt (single-task, narrow scope) with framework scaffolding it doesn't need → structure_score 3-4
- Inflating word count with filler, redundant sections, or restated constraints that add no new information → conciseness_score 4-5
- Rewriting tone or style when the original communication was already clear → conciseness_score 3-4
- Adding constraints that restrict scope beyond the user's original intent (excluding areas or fixing output counts the user left open) → faithfulness_score 4-5

Do NOT penalize these — they are legitimate optimization techniques:
- Codebase-specific references (file paths, line numbers, function signatures) that ground vague instructions in real code
- Scope narrowing that focuses a broad request on high-signal areas backed by domain analysis
- Protective constraints that serve the original goal (preventing low-value output, making implicit criteria explicit)
- Structure proportional to task complexity (a multi-file review warrants more sections than a simple question)
- Long, rich, thoroughly structured prompts where every section adds genuine value — length is not a penalty signal when the content is substantive and non-redundant
- Comprehensive framework application that populates all relevant sections with actionable content

Be rigorous. Most optimizations achieve moderate (5-7) improvement, not strong (8+). Reserve 8+ for optimizations that demonstrably transform the prompt quality.

Focus on whether the optimization actually addresses the weaknesses of the original.

Before the JSON, write one or two sentences stating your key finding about the quality of this optimization."""

    if has_codebase_context:
        base += """

Codebase intelligence is provided (partial navigational context from an explore phase).
Use it to check whether the optimized prompt:
- References real symbol names, function signatures, and file paths that appear in the context
- Does not introduce hallucinated method names, non-existent modules, or fabricated APIs

IMPORTANT: This context is partial and may be stale. Absence of a symbol from this context
does NOT prove it doesn't exist. Only penalize faithfulness_score for identifiers that
clearly contradict what IS shown (e.g., wrong function name when the correct one is visible),
not for referencing things outside the explore coverage.

Scoring calibration for codebase-aware optimization:
- specificity_score: Codebase-grounded references (exact file paths, line numbers, function
  names from analysis) represent the highest tier — reward precision that enables direct
  code navigation
- conciseness_score: Length earned by codebase precision (file:line references replacing
  vague instructions) is not bloat — only penalize redundant or restated content
- faithfulness_score: Informed scope narrowing (focusing a broad request on specific codebase
  areas identified by analysis) serves the original intent when the base prompt is too vague
  to execute as-is"""

    if has_codebase_context and intent_category:
        # Group intents by scoring behavior
        discovery_intents = {"refactoring", "architecture_review"}
        prescriptive_intents = {"debugging", "testing", "feature_build", "security"}

        if intent_category in discovery_intents:
            base += f"""

Intent-specific calibration ({intent_category}):
This is a discovery-oriented prompt — the user wants the executor to IDENTIFY opportunities,
not evaluate pre-identified ones. Score accordingly:
- faithfulness_score: If the optimized prompt pre-identifies specific issues as scope items
  (rather than providing navigational context for the executor to discover issues), treat this
  as a moderate faithfulness concern — the task shifted from discovery to evaluation.
- specificity_score: Navigational context (file paths, module boundaries, data flow) is
  high-value specificity. Prescriptive diagnoses ("X is wrong because Y") are lower-value
  because they constrain the executor's independent assessment."""

        elif intent_category in prescriptive_intents:
            base += f"""

Intent-specific calibration ({intent_category}):
This is a prescriptive-oriented prompt — pre-identifying targets, scope areas, or
investigation paths is appropriate and serves the original intent.
- faithfulness_score: Do NOT penalize pre-identified scope items — for {intent_category},
  directing the executor to specific areas improves precision without reducing faithfulness.
- specificity_score: Prescriptive scope items with file paths and line numbers represent
  the highest tier of specificity for this intent."""

        else:
            specification_intents = {"api_design", "documentation"}
            investigation_intents = {"performance", "migration"}

            if intent_category in specification_intents:
                base += f"""

Intent-specific calibration ({intent_category}):
This is a specification-oriented prompt — the user wants precise definitions, contracts, or
documentation. Score accordingly:
- structure_score: Logical organization of specifications is critical — reward clear section
  hierarchy, consistent field definitions, and systematic coverage. Penalize fragmented or
  arbitrarily ordered specifications.
- specificity_score: Exact endpoint paths, field types, response shapes, and parameter names
  represent the highest tier. Vague 'define the API' instructions are low-value specificity.
- conciseness_score: Specification prompts legitimately require more detail. Do not penalize
  length earned by precise contract definitions — only penalize redundant or restated content."""

            elif intent_category in investigation_intents:
                base += f"""

Intent-specific calibration ({intent_category}):
This is an investigation-oriented prompt — the user wants systematic examination of a specific
concern. Score accordingly:
- specificity_score: References to specific code paths, configuration values, and measurable
  targets (response times, memory limits, version numbers) are the highest tier of specificity.
- structure_score: A clear investigation methodology (what to measure, where to look, what
  thresholds matter) adds structural value. Reward systematic investigation plans.
- faithfulness_score: Scope narrowing to high-impact areas (hot paths for performance,
  high-risk modules for migration) serves the original intent when backed by codebase evidence."""

            elif intent_category and intent_category != "general":
                base += f"""

Intent-specific calibration ({intent_category}):
No specific scoring adjustments for this intent category. Apply the base calibration uniformly."""

    return base
