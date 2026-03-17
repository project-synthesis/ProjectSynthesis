<optimized-prompt>
{{optimized_prompt}}
</optimized-prompt>

<scores>
{{scores}}
</scores>

<weaknesses>
{{weaknesses}}
</weaknesses>

<strategy>
Strategy used: {{strategy_used}}
</strategy>

## Instructions

Generate exactly 3 actionable refinement suggestions for the optimized prompt above.

Each suggestion should be a single, specific instruction the user could give to improve the prompt. Draw from three sources:

1. **Score-driven** — Target the lowest-scoring dimension. Example: "Improve specificity — currently 6.2/10"
2. **Analysis-driven** — Address a weakness detected by the analyzer. Example: "Add error handling constraints"
3. **Strategic** — Apply a technique from the strategy. Example: "Add few-shot examples to demonstrate expected output"

Return exactly 3 suggestions. Each should be actionable in one sentence. Be specific, not vague.

## Output format

Return a JSON object with a single `suggestions` array containing exactly 3 objects. Each object must have:
- `text`: the suggestion as a single actionable sentence
- `source`: one of `"score"`, `"analysis"`, or `"strategy"`
