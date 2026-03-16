# Startup Template Validation — Spec

## Problem

Template placeholder errors (missing `{{variable}}` in a template, or template file missing entirely) are only caught at runtime when the first optimization request hits that code path. This means a broken template can go undetected until a user encounters it.

## Solution

At application startup, validate ALL prompt templates against `manifest.json`:

1. **File existence**: Every template listed in `manifest.json` must exist as a file in `prompts/`.
2. **Placeholder presence**: Every `required` variable in the manifest must appear as `{{variable_name}}` in the template text.
3. **Strategy directory non-empty**: The `prompts/strategies/` directory must contain at least one `.md` file (the analyzer depends on available strategies).
4. **Fast failure**: Any validation error → log ERROR with the specific missing file/placeholder and raise `RuntimeError` to prevent startup.

## Scope

- New method: `PromptLoader.validate_all()` — scans manifest, checks all templates
- New method: `StrategyLoader.validate()` — checks directory non-empty
- Called from `main.py` lifespan (after provider detection, before yielding)
- Tests: verify valid templates pass, missing file fails, missing placeholder fails, empty strategies fails

## Non-Goals

- No runtime re-validation (hot-reload means templates can change after startup)
- No validation of optional variables (they may or may not appear)
- No content quality checks (just structural presence)
