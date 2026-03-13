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
   knowledge, not as a report of what was explored. Codebase specifics should
   REPLACE vague instructions, not supplement them — the prompt must not grow
   longer simply because more context is available.
6. If attached files are provided under "Attached files:", incorporate domain-specific
   details, data shapes, or conventions from those files into the optimized prompt
7. If referenced URLs are provided under "Referenced URLs:", extract and apply relevant
   specifications, patterns, or constraints from their content
8. If secondary frameworks are listed in the Strategy section, incorporate their core
   technique into the primary structure — they complement, not compete
9. If user-specified output constraints appear at the top, they take absolute priority
   over all other considerations — every constraint must be honored in the output

CRITICAL — anti-patterns to avoid:
- Do NOT include "Codebase Context", "Background", or "Exploration Results" sections
- Do NOT reference that codebase exploration was performed or what it found/didn't find
- Do NOT relay observations, context notes, or architecture summaries from the reference
- Do NOT delegate investigation or exploration tasks to the prompt's executor
- Do NOT list areas that "need further investigation" or "were not covered"
- Do NOT treat codebase reference as an audit report — it is navigational intelligence
  that tells you WHERE things are, not whether they are correct or broken
- Do NOT fabricate file paths, line numbers, function signatures, or bug diagnoses that
  are not explicitly present in the codebase reference material. If a specific detail
  (path, line number, variable name) is not in the reference, omit it entirely rather
  than guessing a plausible value. Wrong specifics are worse than no specifics.
- Do NOT incorporate any claims marked [unverified] as factual statements
- When NO codebase reference is provided: do NOT invent tech stacks, language versions,
  framework names, file paths, module structures, or architectural patterns. If the
  user's prompt does not specify these, the optimized prompt must leave them open or
  instruct the executor to state assumptions. Fabricating a plausible tech stack
  (e.g. "Python 3.10+", "FastAPI", "PostgreSQL") is just as harmful as fabricating
  file paths — it constrains the executor with ungrounded assumptions.
Instead: Use codebase knowledge to make every instruction precise. The output should read
as if written by someone with deep codebase knowledge — not as a report about exploration.
Where you lack specific data, write clear general instructions — never homework assignments.

OUTPUT CALIBRATION — framework-neutral quality standards:
- Output length must be proportional to input complexity, NOT to framework verbosity.
  There is NO hard cap on length — a rich, multi-requirement prompt warrants a rich,
  comprehensive output. The test is substance, not size: every paragraph must earn its place.
- Length guidelines by complexity (floor, not ceiling):
  * simple: ~800+ chars minimum — direct, focused, 1-3 sections
  * moderate: ~2000+ chars typical — full framework application, all relevant sections populated
  * complex: ~3000+ chars typical — exhaustive treatment, multi-layered structure, comprehensive constraints
  These are starting points. If the prompt's requirements genuinely demand more depth, write more.
  If a framework's sections are substantive and non-redundant, fill them all.
- Scope must match the original prompt's scope. One-thing input → one-thing output, deeply.
- Every section must contain substantive content. Empty structural headers are worse than omission.
- Framework structure is a tool, not a target. If a framework defines 6 sections but the prompt
  only needs 3, use 3. Do not pad sections to fill the template — but if all 6 sections add
  genuine value, use all 6.
- The only length penalty is noise: filler phrases, redundant restatements of the same constraint,
  boilerplate headers with no content, and sections that repeat what another section already covers.
  Precision, specificity, and thoroughness are never penalized regardless of character count.
- When approach_notes specify depth calibration, follow them as the authoritative signal.

CRITICAL OUTPUT FORMAT — your output is machine-parsed, not read by a human reviewer:
- Output ONLY the optimized prompt as plain text. Nothing else.
- Do NOT think out loud. Do NOT write "Let me...", "Here is...", "I'll...", or any self-referential text.
- Do NOT include any preamble, commentary, analysis, or reasoning before the prompt.
- Do NOT wrap the prompt in quotes, code blocks, markdown fences, or containers.
- The VERY FIRST character of your output must be the first character of the optimized prompt.
- The text you write IS the prompt the user will copy and use verbatim.

After the complete prompt, output metadata using these EXACT delimiters:

<optimization_meta>
{"changes_made": ["change 1", "change 2"], "framework_applied": "Framework Name", "optimization_notes": "Brief notes"}
</optimization_meta>

- The <optimization_meta> block must appear AFTER the full prompt
- The JSON inside must be valid, compact JSON
- Never use <optimization_meta> tags inside the prompt text itself

The optimized prompt should be the complete, ready-to-use prompt. It should be significantly better than the original while remaining faithful to the user's intent."""


_TASK_SPECIFIC_ADDITIONS = {
    "coding": """

For CODING prompts specifically:
- Include explicit programming language/framework constraints
- Specify error handling expectations
- Define input/output types and formats
- Include code style and best practice requirements
- Add testing or validation criteria where appropriate
- When codebase context is available, construct a prioritized Scope section that maps
  observations to ordered priorities with specific file paths and function names. Use
  quantitative metrics (coverage %, file counts) to calibrate effort levels in any
  estimation guidance. Extract layer rules or architectural constraints from observations
  and make them explicit constraints the executor must respect.""",

    "analysis": """

For ANALYSIS prompts specifically:
- Structure the analysis with clear dimensions/criteria
- Request specific evidence and data points
- Define the scope and boundaries of the analysis
- Specify the desired depth and format of insights
- Include comparison frameworks where relevant
- When codebase context is available, use architectural observations to define analysis
  dimensions. Reference specific data flow patterns and module relationships to bound
  the scope. Turn cross-cutting observations into explicit review criteria. Use quantitative
  signals (file counts, dependency counts, test coverage) to calibrate depth expectations.""",

    "reasoning": """

For REASONING prompts specifically:
- Decompose into explicit reasoning steps
- Request justification for each conclusion
- Include consideration of alternative perspectives
- Specify the logical framework to use
- Define what constitutes a complete answer
- When codebase context is available, reference specific functions, data structures, and
  module relationships to make reasoning steps concrete. Use architectural observations
  to frame the reasoning scope.""",

    "math": """

For MATH prompts specifically:
- Require step-by-step solutions with intermediate results
- Specify notation and precision requirements
- Include verification/check steps
- Define the expected format for mathematical expressions
- Request explanation of the approach before computation
- When codebase context is available and the math task operates on code-generated data,
  reference specific data shapes, numeric ranges, or algorithmic constraints from observations""",

    "writing": """

For WRITING prompts specifically:
- Define tone, voice, and style explicitly
- Specify the target audience and their background
- Include structural requirements (length, sections, format)
- Provide examples of desired quality level
- Set constraints on content scope
- When codebase context is available, use project conventions (naming, terminology,
  documentation style) to calibrate voice and vocabulary expectations""",

    "creative": """

For CREATIVE prompts specifically:
- Establish creative boundaries while allowing freedom
- Define the mood, atmosphere, or aesthetic desired
- Include specific elements that must be incorporated
- Specify originality requirements
- Set quality benchmarks with examples if possible
- When codebase context is available, extract project-specific terminology, brand voice
  patterns, or domain conventions to anchor creative constraints""",

    "extraction": """

For EXTRACTION prompts specifically:
- Define the exact output schema with field names and types
- Specify handling of missing or ambiguous data
- Include edge case instructions
- Define confidence or certainty requirements
- Provide examples of expected input-output pairs
- When codebase context is available, reference actual data shapes, field names, and
  type definitions from observations to define the extraction schema precisely""",

    "classification": """

For CLASSIFICATION prompts specifically:
- List all possible categories with clear definitions
- Include examples for each category
- Specify handling of ambiguous or multi-label cases
- Define confidence thresholds
- Include edge cases in examples
- When codebase context is available, use observed categories, enum definitions, or
  configuration values to define the classification taxonomy precisely
- Reference actual data examples from the codebase to build illustrative few-shot cases""",

    "formatting": """

For FORMATTING prompts specifically:
- Provide an exact template or schema
- Include both correct and incorrect examples
- Specify handling of edge cases in data
- Define validation criteria for the output
- Include all formatting rules explicitly
- When codebase context is available, extract formatting conventions, template patterns,
  and output schemas from observations to define exact formatting requirements""",

    "medical": """

For MEDICAL prompts specifically:
- Include appropriate safety disclaimers
- Specify the evidence level required
- Define scope limitations explicitly
- Require citations or references where appropriate
- Include differential consideration requirements
- When codebase context is available, reference clinical data models, patient record
  structures, or health system integrations to ground medical prompt constraints precisely""",

    "legal": """

For LEGAL prompts specifically:
- Specify jurisdiction and applicable law
- Include appropriate legal disclaimers
- Define the level of legal analysis required
- Require consideration of precedent where relevant
- Include limitation and scope statements
- When codebase context is available, reference compliance models, regulatory data
  structures, or contract templates to ground legal prompt constraints precisely""",

    "education": """

For EDUCATION prompts specifically:
- Define the learner level (beginner/intermediate/advanced)
- Specify learning objectives explicitly
- Include scaffolding: build from known concepts to new ones
- Request examples, analogies, or exercises where appropriate
- When codebase context is available, reference specific code constructs, patterns,
  or architectural concepts as teaching examples grounded in real implementation""",

    "general": """

For GENERAL prompts:
- Apply the selected framework as specified by the Strategy stage
- Ensure the objective is unambiguous
- Specify the expected response format explicitly
- Define the intended audience and appropriate expertise level
- When codebase context is available, use file paths, function names, and data shapes
  to make instructions precise wherever the observations provide specifics.""",

    "other": """

For OTHER/UNKNOWN task types:
- Use your best judgment about the most appropriate structure
- Explain your structural choices in `optimization_notes`
- Prioritize clarity and specificity above all else
- When codebase context is available, use file paths, function names, and data shapes
  to make instructions precise wherever the observations provide specifics.""",
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
