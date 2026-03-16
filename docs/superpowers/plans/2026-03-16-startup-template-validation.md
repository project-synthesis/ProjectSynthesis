# Startup Template Validation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate all prompt templates against manifest.json at application startup — catch missing files and missing placeholders before the first request.

**Architecture:** `PromptLoader.validate_all()` iterates manifest entries, checks file existence and placeholder presence. `StrategyLoader.validate()` checks directory non-empty. Both called from `main.py` lifespan. Validation errors raise `RuntimeError` to prevent startup.

**Tech Stack:** Python 3.12+, existing PromptLoader/StrategyLoader

---

## File Structure

| File | Changes |
|------|---------|
| `backend/app/services/prompt_loader.py` | Add `validate_all()` method |
| `backend/app/services/strategy_loader.py` | Add `validate()` method |
| `backend/app/main.py` | Call validation in lifespan |
| `backend/tests/test_prompt_loader.py` | Add validation tests |
| `backend/tests/test_strategy_loader.py` | Add validation test |

---

### Task 1: PromptLoader.validate_all()

**Files:**
- Modify: `backend/app/services/prompt_loader.py`
- Modify: `backend/tests/test_prompt_loader.py`

- [ ] **Step 1: Write validation tests**

```python
class TestStartupValidation:
    def test_validate_all_passes_with_valid_templates(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        loader.validate_all()  # should not raise

    def test_validate_all_fails_missing_file(self, tmp_prompts):
        # Add a manifest entry for a file that doesn't exist
        import json
        manifest = json.loads((tmp_prompts / "manifest.json").read_text())
        manifest["nonexistent.md"] = {"required": ["foo"], "optional": []}
        (tmp_prompts / "manifest.json").write_text(json.dumps(manifest))
        loader = PromptLoader(tmp_prompts)
        with pytest.raises(RuntimeError, match="Template file missing"):
            loader.validate_all()

    def test_validate_all_fails_missing_placeholder(self, tmp_prompts):
        # Template exists but missing a required placeholder
        (tmp_prompts / "broken.md").write_text("No placeholders here.")
        import json
        manifest = json.loads((tmp_prompts / "manifest.json").read_text())
        manifest["broken.md"] = {"required": ["missing_var"], "optional": []}
        (tmp_prompts / "manifest.json").write_text(json.dumps(manifest))
        loader = PromptLoader(tmp_prompts)
        with pytest.raises(RuntimeError, match="missing required variable.*missing_var"):
            loader.validate_all()

    def test_validate_all_skips_empty_required(self, tmp_prompts):
        # Static templates with no required vars should pass
        (tmp_prompts / "static2.md").write_text("Just text, no vars.")
        import json
        manifest = json.loads((tmp_prompts / "manifest.json").read_text())
        manifest["static2.md"] = {"required": [], "optional": []}
        (tmp_prompts / "manifest.json").write_text(json.dumps(manifest))
        loader = PromptLoader(tmp_prompts)
        loader.validate_all()  # should not raise
```

- [ ] **Step 2: Implement validate_all()**

Add to `PromptLoader`:

```python
def validate_all(self) -> None:
    """Validate all templates listed in manifest.json at startup.

    Checks: (1) file exists, (2) all required placeholders present.
    Raises RuntimeError on any validation failure.
    """
    manifest = self.manifest
    errors = []

    for template_name, spec in manifest.items():
        path = self.prompts_dir / template_name

        # Check file exists
        if not path.exists():
            errors.append(f"Template file missing: {template_name}")
            continue

        # Check required placeholders
        content = path.read_text()
        for required_var in spec.get("required", []):
            placeholder = "{{" + required_var + "}}"
            if placeholder not in content:
                errors.append(
                    f"Template '{template_name}' missing required variable {placeholder}"
                )

    if errors:
        msg = "Startup template validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        logger.error(msg)
        raise RuntimeError(msg)

    logger.info("Template validation passed: %d templates verified", len(manifest))
```

- [ ] **Step 3: Run tests, commit**

---

### Task 2: StrategyLoader.validate() + Lifespan Wiring

**Files:**
- Modify: `backend/app/services/strategy_loader.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_strategy_loader.py`

- [ ] **Step 1: Add strategy validation test**

```python
def test_validate_passes_with_strategies(self, tmp_strategies):
    loader = StrategyLoader(tmp_strategies)
    loader.validate()  # should not raise

def test_validate_fails_when_empty(self, tmp_path):
    empty_dir = tmp_path / "strategies"
    empty_dir.mkdir()
    loader = StrategyLoader(empty_dir)
    with pytest.raises(RuntimeError, match="No strategy files"):
        loader.validate()
```

- [ ] **Step 2: Implement validate()**

```python
def validate(self) -> None:
    """Verify strategies directory is non-empty. Raises RuntimeError if empty."""
    strategies = self.list_strategies()
    if not strategies:
        raise RuntimeError(
            f"No strategy files found in {self.strategies_dir}. "
            "At least one .md file is required."
        )
    logger.info("Strategy validation passed: %d strategies available", len(strategies))
```

- [ ] **Step 3: Wire into main.py lifespan**

In the lifespan function, after provider detection and before `yield`:

```python
# Validate prompt templates at startup
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader
loader = PromptLoader(PROMPTS_DIR)
loader.validate_all()
StrategyLoader(PROMPTS_DIR / "strategies").validate()
```

- [ ] **Step 4: Run full suite, commit**
