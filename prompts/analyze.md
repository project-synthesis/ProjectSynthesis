<user-prompt>
{{raw_prompt}}
</user-prompt>

<available-strategies>
{{available_strategies}}
</available-strategies>

## Instructions

You are an expert prompt analyst. Classify the user's prompt and identify its strengths and weaknesses.

Analyze the prompt above and determine:

1. **Task type** — What kind of task is this prompt for? Choose one: coding, writing, analysis, creative, data, system, general.
2. **Intent label** — A concise 3-6 word phrase describing the core intent of this prompt (e.g., "dependency injection refactoring", "API error handling", "landing page layout"). Be specific enough that two prompts with the same intent label are truly about the same thing.
3. **Domain** — Use the format "primary: qualifier" where the primary domain is one of: {{known_domains}}. The qualifier adds specificity about the cross-cutting concern or sub-area (e.g., "backend: auth middleware", "frontend: accessibility"). If no qualifier applies, use just the primary domain. Use "general" only for prompts that don't fit any specific domain.
4. **Weaknesses** — List specific, actionable problems. Be concrete: "no output format specified" not "could be improved."
5. **Strengths** — What does this prompt already do well? Even weak prompts have strengths.
6. **Strategy** — Select the single best strategy from the available list above. If unsure, select "auto."
7. **Rationale** — Explain in 1-2 sentences why this strategy fits.
8. **Confidence** — How confident are you? 0.0 = pure guess, 1.0 = certain. Below 0.7 triggers automatic fallback to "auto" strategy.

Think thoroughly about the prompt's intent and context before classifying. Consider who would write this prompt and what outcome they expect.
