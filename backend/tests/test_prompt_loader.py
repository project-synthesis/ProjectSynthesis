import json
import pytest
from pathlib import Path
from app.services.prompt_loader import PromptLoader


@pytest.fixture
def tmp_prompts(tmp_path):
    """Create a temporary prompts directory with test templates."""
    (tmp_path / "test.md").write_text(
        "<user-prompt>\n{{raw_prompt}}\n</user-prompt>\n\n"
        "<context>\n{{codebase_context}}\n</context>\n\n"
        "## Instructions\nDo the thing."
    )
    (tmp_path / "static.md").write_text("You are a helpful assistant.")
    manifest = {
        "test.md": {"required": ["raw_prompt"], "optional": ["codebase_context"]},
        "static.md": {"required": [], "optional": []},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


class TestPromptLoader:
    def test_load_static(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result = loader.load("static.md")
        assert result == "You are a helpful assistant."

    def test_render_with_variables(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result = loader.render("test.md", {"raw_prompt": "Write a function", "codebase_context": "file.py: def foo():"})
        assert "Write a function" in result
        assert "file.py: def foo():" in result

    def test_optional_var_removed_with_empty_tags(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result = loader.render("test.md", {"raw_prompt": "test"})
        assert "<context>" not in result
        assert "test" in result

    def test_missing_required_var_raises(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        with pytest.raises(ValueError, match="Required variable.*raw_prompt"):
            loader.render("test.md", {})

    def test_unknown_template_raises(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent.md")

    def test_hot_reload(self, tmp_prompts):
        loader = PromptLoader(tmp_prompts)
        result1 = loader.load("static.md")
        (tmp_prompts / "static.md").write_text("Updated content")
        result2 = loader.load("static.md")
        assert result2 == "Updated content"
