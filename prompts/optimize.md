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
- **Evaluate the weaknesses** identified in the analysis. Address genuine weaknesses that improve the prompt, but use your judgment — if a weakness is irrelevant to the user's intent or would shift the prompt away from its goal, skip it. Do not blindly obey the analyzer's checklist.
- **Apply the strategy** — use its techniques to improve the prompt's effectiveness. The strategy takes precedence over the default guidelines below when they conflict (e.g., a chain-of-thought strategy may require more verbosity than the conciseness rule allows — that's fine).
- **Be concise** where the strategy permits. Remove filler words and unnecessary elaboration, but do not sacrifice the strategy's requirements for brevity.
- **Add structure** using markdown `##` headers to delineate sections (e.g. `## Task`, `## Requirements`, `## Constraints`, `## Output`). Use bullet lists for enumerations, numbered lists for sequential steps, and fenced code blocks for signatures, examples, and schemas.
- **Include constraints** the original prompt implies but doesn't state (language, format, error handling, edge cases).
- **Use specific language.** Replace "handle errors" with "raise ValueError with descriptive message on invalid input."

**Context anchoring:** Always anchor the optimized prompt to the technologies, frameworks, and architecture patterns found in the `<codebase-context>` block above, even if the user's original request is generic. Ground abstract requirements in the concrete stack, naming conventions, and design patterns from the workspace. Reference specific services, file paths, and patterns from the context — not generic best practices. If the codebase context is thin or empty, state explicit technology assumptions based on the workspace profile (languages, frameworks) and mark them as assumptions the user can override.

If applied meta-patterns are provided above, integrate their techniques into the optimized prompt where they naturally fit. These are proven patterns from past successful optimizations — use them as guidance, not rigid templates.

After the optimized prompt, add a `## Changes` section summarizing what you changed and why. Use rich markdown formatting — choose the format that best fits the changes:
- **Table** (`| Change | Reason |`) for many small, discrete changes
- **Numbered list** with **bold lead** (e.g., `1. **Added task framing** — removes ambiguity...`) for sequential or prioritized changes
- **Nested bullets** with categories for complex structural rewrites

Be specific in each entry (e.g., "Added explicit task framing with role assignment" not "Improved clarity").
