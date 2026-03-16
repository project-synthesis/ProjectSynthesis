
import pytest

from app.services.strategy_loader import StrategyLoader


@pytest.fixture
def tmp_strategies(tmp_path):
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "chain-of-thought.md").write_text("# Chain of Thought\nThink step by step.")
    (strat_dir / "few-shot.md").write_text("# Few-Shot\nProvide examples.")
    (strat_dir / "auto.md").write_text("# Auto\nSelect the best approach.")
    return strat_dir


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

    def test_load_unknown_strategy_raises(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        with pytest.raises(FileNotFoundError, match="Strategy.*not found"):
            loader.load("nonexistent")

    def test_format_available_strategies(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        formatted = loader.format_available()
        assert "chain-of-thought" in formatted
        assert "few-shot" in formatted

    def test_empty_directory(self, tmp_path):
        strat_dir = tmp_path / "strategies"
        strat_dir.mkdir()
        loader = StrategyLoader(strat_dir)
        assert loader.list_strategies() == []

    def test_validate_passes(self, tmp_strategies):
        loader = StrategyLoader(tmp_strategies)
        loader.validate()

    def test_validate_fails_empty(self, tmp_path):
        empty = tmp_path / "strategies"
        empty.mkdir()
        loader = StrategyLoader(empty)
        with pytest.raises(RuntimeError, match="No strategy files"):
            loader.validate()
