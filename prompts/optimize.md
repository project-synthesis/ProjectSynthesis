<user-prompt>
{{raw_prompt}}
</user-prompt>

<analysis>
{{analysis_summary}}
</analysis>

<codebase-context>
{{codebase_guidance}}
{{codebase_context}}
</codebase-context>

<adaptation>
{{adaptation_state}}
</adaptation>

<applied-meta-patterns>
{{applied_patterns}}
</applied-meta-patterns>

<strategy>
{{strategy_instructions}}
</strategy>

## Instructions

You are an expert prompt engineer. Rewrite the user's prompt using the strategy and analysis above.

**Guidelines:**
- **Preserve intent completely.** The optimized prompt must accomplish the exact same goal.
- **Target the weaknesses** identified in the analysis. Each weakness should be addressed.
- **Apply the strategy** — use its techniques to improve the prompt's effectiveness.
- **Be concise.** Remove filler words, redundant phrases, and unnecessary elaboration. Every word must earn its place.
- **Add structure** using markdown `##` headers to delineate sections (e.g. `## Task`, `## Requirements`, `## Constraints`, `## Output`). Use bullet lists for enumerations, numbered lists for sequential steps, and fenced code blocks for signatures, examples, and schemas.
- **Include constraints** the original prompt implies but doesn't state (language, format, error handling, edge cases).
- **Use specific language.** Replace "handle errors" with "raise ValueError with descriptive message on invalid input."

If the original prompt references a codebase (see context above), incorporate relevant code patterns, naming conventions, and architecture details into the optimized prompt.

If applied meta-patterns are provided above, integrate their techniques into the optimized prompt where they naturally fit. These are proven patterns from past successful optimizations — use them as guidance, not rigid templates.

Summarize the changes you made and why.
