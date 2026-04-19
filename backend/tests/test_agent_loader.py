"""Tests for AgentLoader — seed agent file parsing and registry."""

from pathlib import Path

import pytest

from app.services.agent_loader import AgentLoader


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "seed-agents"
    d.mkdir()
    return d


@pytest.fixture
def sample_agent(agents_dir: Path) -> Path:
    f = agents_dir / "test-coding.md"
    f.write_text(
        "---\n"
        "name: test-coding\n"
        "description: Test coding agent\n"
        "task_types: coding, system\n"
        "phase_context: build\n"
        "prompts_per_run: 5\n"
        "enabled: true\n"
        "---\n\n"
        "You are a test agent.\n\nGenerate {{prompts_per_run}} prompts.\n"
    )
    return f


class TestAgentLoader:
    def test_list_agents(self, agents_dir: Path, sample_agent: Path) -> None:
        loader = AgentLoader(agents_dir)
        agents = loader.list_agents()
        assert len(agents) == 1
        assert agents[0] == "test-coding"

    def test_load_agent(self, agents_dir: Path, sample_agent: Path) -> None:
        loader = AgentLoader(agents_dir)
        agent = loader.load("test-coding")
        assert agent is not None
        assert agent.name == "test-coding"
        assert agent.description == "Test coding agent"
        assert agent.task_types == ["coding", "system"]
        assert agent.phase_context == ["build"]
        assert agent.prompts_per_run == 5
        assert agent.enabled is True
        assert "You are a test agent" in agent.body

    def test_load_missing_agent(self, agents_dir: Path) -> None:
        loader = AgentLoader(agents_dir)
        agent = loader.load("nonexistent")
        assert agent is None

    def test_disabled_agent_excluded_from_enabled(self, agents_dir: Path) -> None:
        f = agents_dir / "disabled-agent.md"
        f.write_text(
            "---\n"
            "name: disabled-agent\n"
            "description: Disabled\n"
            "task_types: coding\n"
            "phase_context: build\n"
            "enabled: false\n"
            "---\n\nBody.\n"
        )
        loader = AgentLoader(agents_dir)
        enabled = loader.list_enabled()
        assert "disabled-agent" not in [a.name for a in enabled]

    def test_list_enabled(self, agents_dir: Path, sample_agent: Path) -> None:
        loader = AgentLoader(agents_dir)
        enabled = loader.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].name == "test-coding"

    def test_defaults_for_missing_fields(self, agents_dir: Path) -> None:
        f = agents_dir / "minimal.md"
        f.write_text(
            "---\n"
            "name: minimal\n"
            "description: Minimal agent\n"
            "task_types: coding\n"
            "phase_context: build\n"
            "---\n\nBody.\n"
        )
        loader = AgentLoader(agents_dir)
        agent = loader.load("minimal")
        assert agent is not None
        assert agent.prompts_per_run == 6  # default
        assert agent.enabled is True  # default
        assert agent.model is None  # default — orchestrator picks MODEL_HAIKU

    def test_model_override_sonnet(self, agents_dir: Path) -> None:
        """Frontmatter ``model: sonnet`` routes this agent to Sonnet."""
        f = agents_dir / "sonnet-agent.md"
        f.write_text(
            "---\n"
            "name: sonnet-agent\n"
            "description: Diversity test agent\n"
            "task_types: creative\n"
            "phase_context: build\n"
            "model: sonnet\n"
            "---\n\nBody.\n"
        )
        loader = AgentLoader(agents_dir)
        agent = loader.load("sonnet-agent")
        assert agent is not None
        assert agent.model == "sonnet"

    def test_model_override_invalid_falls_back_to_default(self, agents_dir: Path) -> None:
        """Unknown model values fall back to None (orchestrator default)."""
        f = agents_dir / "bogus.md"
        f.write_text(
            "---\n"
            "name: bogus\n"
            "description: Bogus agent\n"
            "task_types: coding\n"
            "phase_context: build\n"
            "model: gpt-4\n"
            "---\n\nBody.\n"
        )
        loader = AgentLoader(agents_dir)
        agent = loader.load("bogus")
        assert agent is not None
        assert agent.model is None

    def test_model_override_case_insensitive(self, agents_dir: Path) -> None:
        """Frontmatter model values are normalized to lowercase."""
        f = agents_dir / "opus.md"
        f.write_text(
            "---\n"
            "name: opus\n"
            "description: Opus agent\n"
            "task_types: analysis\n"
            "phase_context: build\n"
            "model: OPUS\n"
            "---\n\nBody.\n"
        )
        loader = AgentLoader(agents_dir)
        agent = loader.load("opus")
        assert agent is not None
        assert agent.model == "opus"
