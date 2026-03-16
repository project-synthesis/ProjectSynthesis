<user-prompt>
{{raw_prompt}}
</user-prompt>

<file-paths>
{{file_paths}}
</file-paths>

<file-contents>
{{file_contents}}
</file-contents>

## Instructions

You are analyzing a codebase to provide relevant context for prompt optimization.

Given the user's prompt above and the repository files shown, extract:

1. **Relevant patterns** — Code conventions, naming patterns, architecture decisions that relate to the user's prompt
2. **Key files** — Which files are most relevant and why
3. **Technical context** — Framework versions, libraries used, coding style that should inform the optimized prompt
4. **Constraints** — Any project-specific constraints (error handling patterns, type requirements, testing conventions)

Be concise. Focus only on information that would help optimize the user's prompt. Do not describe the entire codebase — only the parts relevant to the prompt's task.

Return a structured summary that can be injected as codebase context into the optimization prompt.
