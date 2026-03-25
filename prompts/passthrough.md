<user-prompt>
{{raw_prompt}}
</user-prompt>

<analysis-summary>
{{analysis_summary}}
</analysis-summary>

<codebase-context>
{{codebase_guidance}}
{{codebase_context}}
</codebase-context>

<applied-patterns>
{{applied_patterns}}
</applied-patterns>

<adaptation>
{{adaptation_state}}
</adaptation>

<strategy>
{{strategy_instructions}}
</strategy>

<scoring-rubric>
{{scoring_rubric_excerpt}}
</scoring-rubric>

## Instructions

You are an expert prompt engineer. Optimize the user's prompt above, then score your optimized version.

**Optimization guidelines:**
- Preserve the original intent completely
- Add structure, constraints, and specificity
- Remove filler and redundancy
- Apply the strategy above (if provided)
- Use the analysis summary above (if provided) to address identified weaknesses
- Incorporate proven patterns above (if provided) where applicable

**Output format for the optimized prompt:**
Always structure the optimized prompt using markdown `##` headers to delineate sections (e.g. `## Task`, `## Requirements`, `## Constraints`, `## Output`). Use bullet lists (`-`) for enumerations, numbered lists (`1.`) for sequential steps, and fenced code blocks for signatures, examples, and schemas. This ensures consistent rendering regardless of which strategy was applied.

**Scoring guidelines:**
Score the OPTIMIZED prompt on 5 dimensions (1.0-10.0 each, decimals encouraged). Use the scoring rubric above for calibration.

- **clarity** — How unambiguous is the prompt? (1=riddled with ambiguity, 10=crystal clear)
- **specificity** — How many constraints and details are provided? (1=vague, 10=fully constrained)
- **structure** — How well-organized is the prompt? (1=wall of text, 10=perfectly sectioned)
- **faithfulness** — Does the optimized prompt preserve the original intent? (1=completely different, 10=perfectly faithful)
- **conciseness** — Is every word necessary? (1=extremely verbose/redundant, 10=maximally concise)

**Important:** Be calibrated and critical. Avoid score inflation — a 7 is good, an 8 is very good, a 9+ is exceptional. Most prompts should score in the 6-8 range.

Return a JSON object with this exact structure (no markdown fences, no commentary outside the JSON):

```json
{
  "optimized_prompt": "The full optimized prompt text...",
  "changes_summary": "Brief description of what changed and why...",
  "task_type": "coding|writing|analysis|creative|data|system|general",
  "strategy_used": "The strategy name you applied",
  "domain": "backend|frontend|database|devops|security|fullstack|general",
  "intent_label": "3-6 word intent description",
  "scores": {
    "clarity": 7.5,
    "specificity": 8.0,
    "structure": 7.0,
    "faithfulness": 9.0,
    "conciseness": 7.5
  }
}
```
