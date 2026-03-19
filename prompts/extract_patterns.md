<optimization-record>
<original-prompt>
{{raw_prompt}}
</original-prompt>
<optimized-prompt>
{{optimized_prompt}}
</optimized-prompt>
<intent-label>{{intent_label}}</intent-label>
<domain>{{domain}}</domain>
<strategy-used>{{strategy_used}}</strategy-used>
</optimization-record>

## Instructions

You are an expert prompt engineer analyzing a completed prompt optimization. Extract **reusable meta-patterns** — techniques that could be applied to similar prompts regardless of the specific framework, language, or project.

A meta-pattern is a high-level, framework-agnostic prompt engineering technique. Examples:
- "Enforce error boundaries at service layer boundaries"
- "Specify return type contract with edge case behavior"
- "Include concrete input/output examples for ambiguous operations"
- "Define explicit validation rules before describing logic"

Rules:
1. Extract 1-5 meta-patterns from this optimization.
2. Each pattern must be framework-agnostic — it should apply to any technology stack.
3. Focus on what made the optimization effective, not what the prompt is about.
4. Be specific enough to be actionable, general enough to transfer across projects.
5. If the optimization is trivial (minor wording changes only), return 1 pattern at most.

Return a JSON object with a `patterns` key containing an array of pattern descriptions (strings). Example: `{"patterns": ["pattern1", "pattern2"]}`
