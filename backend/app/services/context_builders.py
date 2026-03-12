"""Context summary builders for pipeline stages.

These functions produce human-readable context blocks injected into LLM prompts
at each pipeline stage. They summarise the output from previous stages in a
structured, token-efficient format.

Round 2 established the base implementations; Round 3 extends them:
  N13 — secondary_frameworks propagated through build_strategy_summary()
  N15 — codebase_informed quality note added to build_analysis_summary()

Round 4 (N20) fixes three silent data-loss bugs in build_codebase_summary():
  key_files_read (was "key_files"), grounding_notes (was "notes"),
  relevant_snippets as list (was "snippets" as dict)

Round 5 (N36) extends build_codebase_summary():
  branch and files_read_count are now surfaced; snippet cap raised 200→400 chars.

Round 6 (P1.5) further improves build_codebase_summary():
  - Snippet count cap raised 3→5
  - Snippet content cap raised 400→600 chars
  - Observations cap raised 5→8
  - Grounding notes cap raised 5→8
  - coverage_pct surfaced; repo and branch merged into one header line
  - Quality warnings are now specific: partial vs failed give distinct messages

Round 7 (P2.2) extends build_analysis_summary():
  analysis_quality caveat prepended when quality is 'fallback' or 'failed'

Round 8 (P3) raises build_codebase_summary() caps and adds intent header:
  - Tech stack cap raised 10→15
  - Key files cap raised 10→20
  - Observations cap raised 8→12
  - Grounding notes cap raised 8→12
  - Snippet count cap raised 5→10
  - Snippet content cap raised 600→1200 chars
  - Intent focus header added (shown when intent_category is set and not 'general')
"""


def build_codebase_summary(codebase_context: dict) -> str:
    """Build a human-readable summary of codebase exploration results.

    Includes specific quality warning (partial/failed), repository @ branch header,
    intent focus header (when intent_category is set and not 'general'),
    files_read_count, coverage_pct (when >0), tech stack (capped at 15),
    key_files_read (capped at 20), observations and grounding_notes (capped
    at 12 each), and up to 10 relevant_snippets (content capped at 1200 chars).
    """
    if not codebase_context:
        return ""

    parts: list[str] = []

    quality = codebase_context.get("explore_quality", "complete")
    files_read_count = codebase_context.get("files_read_count") or 0
    coverage_pct = codebase_context.get("coverage_pct") or 0

    if quality == "partial" and files_read_count == 0:
        # Timed out before reading any files — effectively no data available.
        parts.append(
            "Note: No codebase data available. Write the prompt based on "
            "the raw input alone — do not reference or delegate codebase exploration."
        )
    elif quality == "partial":
        parts.append(
            f"Note: Coverage limited to {files_read_count} files "
            f"({coverage_pct}% of repository). This is navigational context only — "
            "use it to write precise instructions where you have data; "
            "write clear general instructions where you don't. "
            "Never delegate exploration and never relay explore-phase findings."
        )
    elif quality == "failed":
        parts.append(
            "Note: No codebase data available. Write the prompt based on "
            "the raw input alone — do not reference or delegate codebase exploration."
        )

    repo = codebase_context.get("repo")
    branch = codebase_context.get("branch")
    if repo and branch:
        parts.append(f"Repo: {repo} @ {branch}")
    elif repo:
        parts.append(f"Repo: {repo}")
    elif branch:
        parts.append(f"Branch: {branch}")

    intent = codebase_context.get("intent_category")
    depth = codebase_context.get("depth", "")
    if intent and intent != "general":
        parts.append(f"Intent focus: {intent}" + (f" (depth: {depth})" if depth else ""))

    if files_read_count:
        parts.append(f"Files read: {files_read_count}")

    if coverage_pct > 0:
        parts.append(f"Coverage: {coverage_pct}% of repository")

    tech_stack = codebase_context.get("tech_stack", [])
    if tech_stack:
        parts.append(f"Tech stack: {', '.join(str(t) for t in tech_stack[:15])}")

    key_files = codebase_context.get("key_files_read", [])      # was "key_files"
    if key_files:
        parts.append(f"Key files: {', '.join(str(f) for f in key_files[:20])}")

    observations = codebase_context.get("observations", [])
    if observations:
        parts.append("Architecture (structural observations, not correctness judgments):")
        for obs in list(observations)[:12]:
            parts.append(f"  - {obs}")

    grounding_notes = codebase_context.get("grounding_notes", [])   # was "notes"
    if grounding_notes:
        parts.append("Context intelligence (navigation hints for executor):")
        for note in list(grounding_notes)[:12]:
            parts.append(f"  - {note}")

    snippets = codebase_context.get("relevant_snippets", [])     # was "snippets" (dict)
    if snippets and isinstance(snippets, list):
        parts.append("Key snippets:")
        for snip in snippets[:10]:
            file_name = snip.get("file", "?")
            lines = snip.get("lines", "")
            ctx_text = str(snip.get("context", ""))[:1200]
            loc = f"{file_name}:{lines}" if lines else file_name
            parts.append(f"  [{loc}]: {ctx_text}")

    return "\n".join(parts)


def build_analysis_summary(analysis: dict) -> str:
    """Build a human-readable summary of prompt analysis results.

    Includes task type, complexity, weaknesses, strengths, recommended
    frameworks.  When codebase_informed is 'partial' or False/failed, appends
    a note so downstream stages know the analysis quality (N15).
    """
    if not analysis:
        return ""

    parts: list[str] = []

    analysis_quality = analysis.get("analysis_quality")
    if analysis_quality == "fallback":
        parts.append(
            "Note: Prompt analysis fell back to defaults (LLM call failed). "
            "Framework selection should prioritize well-established patterns."
        )
        parts.append("")  # blank line separator
    elif analysis_quality == "failed":
        parts.append(
            "Note: Prompt analysis failed completely. Proceed with maximum caution."
        )
        parts.append("")  # blank line separator

    task_type = analysis.get("task_type")
    if task_type:
        parts.append(f"Task type: {task_type}")

    complexity = analysis.get("complexity")
    if complexity:
        parts.append(f"Complexity: {complexity}")

    weaknesses = analysis.get("weaknesses", [])
    if weaknesses:
        parts.append("Weaknesses:")
        for w in weaknesses:
            parts.append(f"  - {w}")

    strengths = analysis.get("strengths", [])
    if strengths:
        parts.append("Strengths:")
        for s in strengths:
            parts.append(f"  + {s}")

    recommended = analysis.get("recommended_frameworks", [])
    if recommended:
        parts.append(f"Recommended frameworks: {', '.join(str(f) for f in recommended)}")

    # N15: codebase_informed quality note — True is clean case, omit to avoid noise
    codebase_informed = analysis.get("codebase_informed")
    if codebase_informed == "partial":
        parts.append("[NOTE: Analysis was only partially informed by codebase exploration]")
    elif codebase_informed is False or codebase_informed == "failed":
        parts.append("[NOTE: Analysis had no codebase grounding — codebase exploration failed]")

    return "\n".join(parts)


def build_strategy_summary(strategy: dict) -> str:
    """Build a human-readable summary of strategy selection results.

    Includes primary framework, rationale, and approach notes.  When secondary
    frameworks are present, appends them with an explicit integration directive
    so the optimizer weaves them into the primary structure (N13).
    """
    if not strategy:
        return ""

    parts: list[str] = []

    primary = strategy.get("primary_framework")
    if primary:
        parts.append(f"Primary framework: {primary}")

    rationale = strategy.get("rationale")
    if rationale:
        parts.append(f"Rationale: {rationale}")

    approach_notes = strategy.get("approach_notes")
    if approach_notes:
        parts.append(f"Approach: {approach_notes}")

    # N13: secondary_frameworks — explicitly included so the optimizer applies them
    secondary = strategy.get("secondary_frameworks", [])
    if secondary:
        parts.append(f"Secondary frameworks: {', '.join(str(f) for f in secondary)}")
        parts.append(
            "  (Weave these techniques into the primary structure"
            " — do not create parallel sections)"
        )

    return "\n".join(parts)


# ── Shared context injection helpers ──────────────────────────────────────────

# Public caps — used by pipeline.py for global truncation and by the format_*
# helpers below for per-call safety.  Keeping them in one place prevents
# silent divergence when a cap is changed.
MAX_FILE_CONTEXTS = 5
MAX_URL_CONTEXTS = 3
MAX_INSTRUCTIONS = 10
_MAX_CONTENT_CHARS = 1500  # internal — only affects formatting, not pipeline caps


def format_file_contexts(file_contexts: list[dict] | None) -> str:
    """Format attached file contexts into an injection block.

    Returns empty string when there are no file contexts.
    Skips items with missing or empty content (mirrors format_url_contexts).
    """
    if not file_contexts:
        return ""
    blocks = []
    for fc in file_contexts[:MAX_FILE_CONTEXTS]:
        if not fc.get("content"):
            continue
        name = fc.get("name", "file")
        content = str(fc["content"])[:_MAX_CONTENT_CHARS]
        blocks.append(f"[{name}]\n{content}")
    return ("\n\nAttached files:\n" + "\n\n".join(blocks)) if blocks else ""


def format_url_contexts(url_fetched_contexts: list[dict] | None) -> str:
    """Format pre-fetched URL contexts into an injection block.

    Returns empty string when there are no URL contexts or all errored.
    """
    if not url_fetched_contexts:
        return ""
    blocks = []
    for uc in url_fetched_contexts[:MAX_URL_CONTEXTS]:
        if uc.get("error") or not uc.get("content"):
            continue
        url = uc.get("url", "url")
        content = str(uc["content"])[:_MAX_CONTENT_CHARS]
        blocks.append(f"[{url}]\n{content}")
    return ("\n\nReferenced URLs:\n" + "\n\n".join(blocks)) if blocks else ""


def format_instructions(instructions: list[str] | None, *, label: str = "User-specified output constraints") -> str:
    """Format user instructions into an injection block.

    Returns empty string when there are no instructions.
    Caps at :data:`MAX_INSTRUCTIONS` items — the same constant the pipeline
    uses for global context truncation.
    """
    if not instructions:
        return ""
    constraint_block = "\n".join(f"  - {i}" for i in instructions[:MAX_INSTRUCTIONS])
    return f"\n\n{label}:\n{constraint_block}"
