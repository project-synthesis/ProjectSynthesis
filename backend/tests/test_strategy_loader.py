
import pytest

from app.services.strategy_loader import (
    StrategyLoader,
    _parse_frontmatter,
    validate_frontmatter,
)


@pytest.fixture
def tmp_strategies(tmp_path):
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "chain-of-thought.md").write_text(
        "---\ntagline: reasoning\ndescription: Step-by-step reasoning.\n---\n"
        "# Chain of Thought\nThink step by step."
    )
    (strat_dir / "few-shot.md").write_text(
        "---\ntagline: examples\ndescription: Example-driven.\n---\n"
        "# Few-Shot\nProvide examples."
    )
    (strat_dir / "auto.md").write_text(
        "---\ntagline: adaptive\ndescription: Best approach.\n---\n"
        "# Auto\nSelect the best approach."
    )
    return strat_dir


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\ntagline: test\ndescription: A test.\n---\n# Body\nContent."
        meta, body = _parse_frontmatter(content)
        assert meta["tagline"] == "test"
        assert meta["description"] == "A test."
        assert "# Body" in body

    def test_no_frontmatter(self):
        content = "# Just a heading\nSome content."
        meta, body = _parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_frontmatter_block(self):
        content = "---\n---\n# Body\nContent."
        meta, body = _parse_frontmatter(content)
        assert meta == {}
        assert "# Body" in body

    def test_value_with_colons(self):
        content = "---\ndescription: This: has: colons in it.\n---\nBody."
        meta, body = _parse_frontmatter(content)
        assert meta["description"] == "This: has: colons in it."

    def test_line_without_colon_skipped(self):
        content = "---\ntagline: test\nbad line no colon\n---\nBody."
        meta, body = _parse_frontmatter(content)
        assert meta["tagline"] == "test"
        assert "bad line" not in str(meta)

    def test_keys_lowercased(self):
        content = "---\nTagline: Test\nDESCRIPTION: Desc.\n---\nBody."
        meta, body = _parse_frontmatter(content)
        assert meta["tagline"] == "Test"
        assert meta["description"] == "Desc."


class TestValidateFrontmatter:
    def test_valid(self):
        meta = {"tagline": "test", "description": "A description."}
        warnings = validate_frontmatter(meta)
        assert warnings == []

    def test_missing_tagline(self):
        meta = {"description": "Has description."}
        warnings = validate_frontmatter(meta, filename="test")
        assert any("missing 'tagline'" in w for w in warnings)

    def test_missing_description(self):
        meta = {"tagline": "tag"}
        warnings = validate_frontmatter(meta, filename="test")
        assert any("missing 'description'" in w for w in warnings)

    def test_tagline_too_long(self):
        meta = {"tagline": "x" * 50, "description": "ok"}
        warnings = validate_frontmatter(meta)
        assert any("tagline too long" in w for w in warnings)

    def test_description_too_long(self):
        meta = {"tagline": "ok", "description": "x" * 300}
        warnings = validate_frontmatter(meta)
        assert any("description too long" in w for w in warnings)

    def test_unknown_keys(self):
        meta = {"tagline": "ok", "description": "ok", "custom_field": "val"}
        warnings = validate_frontmatter(meta)
        assert any("unknown frontmatter keys" in w for w in warnings)

    def test_empty_meta(self):
        warnings = validate_frontmatter({})
        assert len(warnings) == 2  # missing tagline + missing description


class TestStrategyLoader:
    def test_list_strategies(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        strategies = loader.list_strategies()
        assert "chain-of-thought" in strategies
        assert "few-shot" in strategies
        assert "auto" in strategies

    def test_load_strategy(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        content = loader.load("chain-of-thought")
        assert "Think step by step" in content

    def test_load_strips_frontmatter(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        content = loader.load("chain-of-thought")
        assert "tagline:" not in content
        assert "---" not in content

    def test_load_unknown_returns_fallback(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        content = loader.load("nonexistent")
        assert "No specific strategy" in content

    def test_load_empty_body_returns_fallback(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        (strat_dir / "empty-body.md").write_text("---\ntagline: t\n---\n")
        loader = StrategyLoader(strat_dir)
        content = loader.load("empty-body")
        assert "no instructions" in content.lower()

    def test_load_unreadable_file_returns_fallback(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        bad_file = strat_dir / "bad.md"
        bad_file.write_bytes(b"\x80\x81\x82")  # invalid UTF-8
        loader = StrategyLoader(strat_dir)
        content = loader.load("bad")
        assert "could not be read" in content.lower()

    def test_load_metadata(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        meta = loader.load_metadata("chain-of-thought")
        assert meta["name"] == "chain-of-thought"
        assert meta["tagline"] == "reasoning"
        assert meta["description"] == "Step-by-step reasoning."
        assert meta["warnings"] == []

    def test_load_metadata_missing_frontmatter_has_warnings(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        (strat_dir / "no-fm.md").write_text("# No frontmatter\nJust content.")
        loader = StrategyLoader(strat_dir)
        meta = loader.load_metadata("no-fm")
        assert len(meta["warnings"]) >= 2  # missing tagline + description
        # Fallback description from first line
        assert meta["description"] == "Just content."

    def test_load_metadata_nonexistent(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        loader = StrategyLoader(strat_dir)
        meta = loader.load_metadata("ghost")
        assert "not found" in meta["warnings"][0]

    def test_list_with_metadata(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        all_meta = loader.list_with_metadata()
        assert len(all_meta) == 3
        names = {m["name"] for m in all_meta}
        assert "chain-of-thought" in names

    def test_format_available_strategies(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        formatted = loader.format_available()
        assert "chain-of-thought (reasoning)" in formatted
        assert "few-shot (examples)" in formatted

    def test_empty_directory(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        loader = StrategyLoader(strat_dir)
        assert loader.list_strategies() == []

    def test_empty_directory_load_returns_fallback(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        loader = StrategyLoader(strat_dir)
        content = loader.load("auto")
        assert "No specific strategy" in content

    def test_validate_passes(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        loader.validate()

    def test_validate_empty_warns_not_crashes(self, tmp_path):
        empty = tmp_path / "strategies"
        empty.mkdir()
        loader = StrategyLoader(empty)
        loader.validate()

    def test_validate_warns_on_bad_frontmatter(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        (strat_dir / "bad.md").write_text("# No frontmatter\nContent only.")
        loader = StrategyLoader(strat_dir)
        loader.validate()  # should not raise, just warn

    def test_format_available_empty(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        loader = StrategyLoader(strat_dir)
        assert loader.format_available() == "No strategies available."
