# Meta-Prompting Strategy

Optimize the prompt by making its structure and intent more explicit to the AI.

## Techniques
- Add explicit task framing: "Your task is to..." followed by clear objectives
- Separate context from instructions (data at top, instructions at bottom)
- Add self-check instructions: "Before responding, verify that..."
- Include negative constraints: "Do NOT include...", "Avoid..."
- Specify the audience: "Write for developers who are familiar with..."
- Add quality criteria the response should meet

## When to Use
- General-purpose improvement when no specific strategy fits
- Prompts that are unclear about what they want
- Tasks requiring precision in following instructions
- Complex prompts that need organizational improvement

## When to Avoid
- Prompts that are already well-structured (would add unnecessary meta-instructions)
- Very short, clear tasks (meta-instructions would overwhelm the actual task)
