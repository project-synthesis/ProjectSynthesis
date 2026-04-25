---
tagline: structure
description: Optimize the prompt by making its structure and intent more explicit to the AI.
---

# Meta-Prompting Strategy

Optimize the prompt by making its structure and intent more explicit to the AI.

## Techniques
- Add explicit task framing: "Your task is to..." followed by clear objectives
- Separate context from instructions (data at top, instructions at bottom)
- Add self-check instructions: "Before responding, verify that..."
- Include negative constraints: "Do NOT include...", "Avoid..."
- Specify the audience: "Write for developers who are familiar with..."
- Add quality criteria the response should meet

## Voice discipline (avoid optimizer-thinking leakage)

Every instruction must use **imperative voice** — directives addressed to the executor, never questions the optimizer is asking itself. When decomposing audits / investigations into a closed taxonomy of failure modes or numbered checks, each item ends as an imperative ("Identify X", "Confirm Y", "Find Z", "Distinguish A from B"), not a rhetorical question ("Does X happen?", "Where is Y called?", "Is Z guarded?").

A trailing `?` in an instruction list is the most common leakage pattern: it signals the optimizer was reasoning through possibilities and forgot to convert them into directives. Such reasoning belongs in `changes_summary`, not in the deliverable prompt.

## When to Use
- General-purpose improvement when no specific strategy fits
- Prompts that are unclear about what they want
- Tasks requiring precision in following instructions
- Complex prompts that need organizational improvement

## When to Avoid
- Prompts that are already well-structured (would add unnecessary meta-instructions)
- Very short, clear tasks (meta-instructions would overwhelm the actual task)
