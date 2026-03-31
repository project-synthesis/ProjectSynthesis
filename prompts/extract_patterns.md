<optimization-record>
<original-prompt>
{{raw_prompt}}
</original-prompt>
<optimized-prompt>
{{optimized_prompt}}
</optimized-prompt>
<intent-label>{{intent_label}}</intent-label>
<domain>{{domain_raw}}</domain>
{{taxonomy_context}}
<strategy-used>{{strategy_used}}</strategy-used>
</optimization-record>

## Instructions

You are an expert prompt engineer analyzing a completed prompt optimization. Extract **reusable meta-patterns** — techniques that made this optimization effective and could be applied to similar prompts in the same domain.

A meta-pattern captures a specific, actionable technique. It should be **domain-aware** — grounded in the domain's vocabulary, metrics, and concerns — while being transferable across different projects within that domain.

**Good patterns** (domain-grounded, actionable):
- "Require SaaS-specific metrics (MRR, ARR, churn rate, LTV) as quantitative anchors rather than vague growth references"
- "Decompose multi-stakeholder requests by specifying each audience's decision criteria separately"
- "Define the user lifecycle stage (trial → activation → retention → expansion) before requesting behavioral analysis"
- "Specify the output artifact type (PRD, email sequence, pricing table, dashboard spec) with required sections"

**Bad patterns** (too generic, applies to everything):
- "Be more specific" — not actionable
- "Add structure" — too vague
- "Use clear language" — obvious

Rules:
1. Extract 1-5 meta-patterns from this optimization.
2. Ground patterns in the `<domain>` above — use that domain's specific terminology, metrics, workflows, and deliverables.
3. Focus on **what structural or analytical technique** made the optimization effective, not surface-level formatting.
4. Each pattern should complete the sentence: "When optimizing prompts about [this domain], always..."
5. If the optimization is trivial (minor wording changes only), return 1 pattern at most.

Return a JSON object with a `patterns` key containing an array of pattern descriptions (strings). Example: `{"patterns": ["pattern1", "pattern2"]}`
