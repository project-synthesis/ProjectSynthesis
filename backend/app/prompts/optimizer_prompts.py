"""Stage 3: Optimizer system prompts (per task type)."""


_BASE_OPTIMIZER_PROMPT = """You are an expert prompt engineer. Your task is to rewrite and optimize a raw prompt using the specified framework and strategy.

IMPORTANT INSTRUCTIONS:
1. Apply the specified framework(s) to restructure the prompt
2. Address weaknesses in order of severity (most critical first). If a weakness cannot be addressed without violating a user-specified constraint, skip it and note the unresolved tension in `optimization_notes`.
3. Preserve the original intent and all key requirements
4. Make the prompt more specific, structured, and actionable
5. If codebase context is provided, absorb it as background intelligence — use it
   to make the prompt surgically precise (exact file paths, function signatures,
   data shapes, architectural patterns) but NEVER expose the exploration process.
   The optimized prompt should read as if written by someone with deep codebase
   knowledge, not as a report of what was explored.
6. If attached files are provided under "Attached files:", incorporate domain-specific
   details, data shapes, or conventions from those files into the optimized prompt
7. If referenced URLs are provided under "Referenced URLs:", extract and apply relevant
   specifications, patterns, or constraints from their content
8. If secondary frameworks are listed in the Strategy section, incorporate their core
   technique into the primary structure — they complement, not compete
9. If user-specified output constraints appear at the top, they take absolute priority
   over all other considerations — every constraint must be honored in the output

CRITICAL — anti-patterns to avoid:
- Do NOT include "Codebase Context" or "Background" sections summarizing exploration results
- Do NOT reference that codebase exploration was performed or what it found/didn't find
- Do NOT delegate investigation or exploration tasks to the prompt's executor
- Do NOT list areas that "need further investigation" or "were not covered"
- Do NOT fabricate file paths, line numbers, function signatures, or bug diagnoses that
  are not explicitly present in the codebase reference material. If a specific detail
  (path, line number, variable name) is not in the reference, omit it entirely rather
  than guessing a plausible value. Wrong specifics are worse than no specifics.
Instead: Use codebase knowledge to make every instruction precise. Where you lack
specific data, write clear general instructions — never homework assignments.

You MUST respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{{
  "optimized_prompt": "The full rewritten prompt text",
  "changes_made": ["List of specific changes made and why"],
  "framework_applied": "Name of the primary framework applied",
  "optimization_notes": "Brief notes on the optimization approach"
}}

The optimized_prompt should be the complete, ready-to-use prompt. It should be significantly better than the original while remaining faithful to the user's intent."""


_TASK_SPECIFIC_ADDITIONS = {
    "coding": """

For CODING prompts specifically:
- Include explicit programming language/framework constraints
- Specify error handling expectations
- Define input/output types and formats
- Include code style and best practice requirements
- Add testing or validation criteria where appropriate""",

    "analysis": """

For ANALYSIS prompts specifically:
- Structure the analysis with clear dimensions/criteria
- Request specific evidence and data points
- Define the scope and boundaries of the analysis
- Specify the desired depth and format of insights
- Include comparison frameworks where relevant""",

    "reasoning": """

For REASONING prompts specifically:
- Decompose into explicit reasoning steps
- Request justification for each conclusion
- Include consideration of alternative perspectives
- Specify the logical framework to use
- Define what constitutes a complete answer""",

    "math": """

For MATH prompts specifically:
- Require step-by-step solutions with intermediate results
- Specify notation and precision requirements
- Include verification/check steps
- Define the expected format for mathematical expressions
- Request explanation of the approach before computation""",

    "writing": """

For WRITING prompts specifically:
- Define tone, voice, and style explicitly
- Specify the target audience and their background
- Include structural requirements (length, sections, format)
- Provide examples of desired quality level
- Set constraints on content scope""",

    "creative": """

For CREATIVE prompts specifically:
- Establish creative boundaries while allowing freedom
- Define the mood, atmosphere, or aesthetic desired
- Include specific elements that must be incorporated
- Specify originality requirements
- Set quality benchmarks with examples if possible""",

    "extraction": """

For EXTRACTION prompts specifically:
- Define the exact output schema with field names and types
- Specify handling of missing or ambiguous data
- Include edge case instructions
- Define confidence or certainty requirements
- Provide examples of expected input-output pairs""",

    "classification": """

For CLASSIFICATION prompts specifically:
- List all possible categories with clear definitions
- Include examples for each category
- Specify handling of ambiguous or multi-label cases
- Define confidence thresholds
- Include edge cases in examples""",

    "formatting": """

For FORMATTING prompts specifically:
- Provide an exact template or schema
- Include both correct and incorrect examples
- Specify handling of edge cases in data
- Define validation criteria for the output
- Include all formatting rules explicitly""",

    "medical": """

For MEDICAL prompts specifically:
- Include appropriate safety disclaimers
- Specify the evidence level required
- Define scope limitations explicitly
- Require citations or references where appropriate
- Include differential consideration requirements""",

    "legal": """

For LEGAL prompts specifically:
- Specify jurisdiction and applicable law
- Include appropriate legal disclaimers
- Define the level of legal analysis required
- Require consideration of precedent where relevant
- Include limitation and scope statements""",

    "education": """

For EDUCATION prompts specifically:
- Define the learner level (beginner/intermediate/advanced)
- Specify learning objectives explicitly
- Include scaffolding: build from known concepts to new ones
- Request examples, analogies, or exercises where appropriate""",

    "general": """

For GENERAL prompts:
- Apply CO-STAR structure (Context, Objective, Style, Tone, Audience, Response format)
- Ensure the objective is unambiguous
- Specify the expected response format explicitly
- Define the intended audience and appropriate expertise level""",

    "other": """

For OTHER/UNKNOWN task types:
- Use your best judgment about the most appropriate structure
- Explain your structural choices in `optimization_notes`
- Prioritize clarity and specificity above all else""",
}


def get_optimizer_prompt(task_type: str = "general") -> str:
    """Build the Stage 3 system prompt for prompt optimization.

    Includes task-type-specific guidance when available.
    """
    prompt = _BASE_OPTIMIZER_PROMPT
    addition = _TASK_SPECIFIC_ADDITIONS.get(task_type, "")
    if addition:
        prompt += addition
    return prompt
