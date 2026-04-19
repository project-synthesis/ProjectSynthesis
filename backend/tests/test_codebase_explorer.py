"""Tests for CodebaseExplorer — semantic retrieval + synthesis."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.codebase_explorer import CodebaseExplorer, ExploreOutput


def _make_prompt_loader(tmp_path):
    """Create a PromptLoader with a minimal explore.md template."""
    from app.services.prompt_loader import PromptLoader

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Minimal explore.md template
    (prompts_dir / "explore.md").write_text(
        "<user-prompt>\n{{raw_prompt}}\n</user-prompt>\n\n"
        "<file-paths>\n{{file_paths}}\n</file-paths>\n\n"
        "<file-contents>\n{{file_contents}}\n</file-contents>\n\n"
        "## Instructions\n\nAnalyze the codebase.\n"
    )

    # Static explore system prompt
    (prompts_dir / "explore-guidance.md").write_text(
        "You are a codebase analysis assistant. Be concise.\n"
    )

    # Manifest declaring required variables
    import json
    (prompts_dir / "manifest.json").write_text(json.dumps({
        "explore.md": {
            "required": ["raw_prompt", "file_contents", "file_paths"],
            "optional": [],
        },
        "explore-guidance.md": {
            "required": [],
            "optional": [],
        },
    }))

    return PromptLoader(prompts_dir)


def _make_github_client(tree_items=None, file_contents=None):
    """Create a mocked GitHubClient."""
    gc = AsyncMock()
    gc.get_branch_head_sha.return_value = "sha_abc123"
    gc.get_tree.return_value = tree_items or [
        {"type": "blob", "path": "src/main.py", "sha": "f1", "size": 100},
        {"type": "blob", "path": "src/utils.py", "sha": "f2", "size": 200},
    ]
    gc.get_file_content.return_value = file_contents or "def main():\n    pass\n"
    return gc


def _make_embedding_service(should_raise=False):
    """Create a mocked EmbeddingService."""
    es = MagicMock()
    if should_raise:
        es.embed_single.side_effect = RuntimeError("model not loaded")
    else:
        zero_vec = np.zeros(384, dtype=np.float32)
        es.embed_single.return_value = zero_vec
        es.cosine_search.return_value = [(0, 0.95), (1, 0.80)]
    return es


def _make_provider(context_text="Codebase uses FastAPI with async patterns."):
    """Create a mocked LLMProvider."""
    provider = AsyncMock()
    provider.complete_parsed.return_value = ExploreOutput(context=context_text)
    return provider


# ---------------------------------------------------------------------------
# Test 1: explore returns context string on success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_returns_context(tmp_path):
    loader = _make_prompt_loader(tmp_path)
    gc = _make_github_client()
    es = _make_embedding_service()
    provider = _make_provider()

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    result = await explorer.explore(
        raw_prompt="Write a FastAPI endpoint",
        repo_full_name="owner/repo",
        branch="main",
        token="ghp_test",
    )

    assert result is not None
    assert isinstance(result, str)
    assert "FastAPI" in result

    # Verify provider was called with Sonnet model (explore synthesis is
    # long-context reading comprehension — Sonnet's strength, not Haiku's).
    provider.complete_parsed.assert_awaited_once()
    call_kwargs = provider.complete_parsed.call_args
    model_value = call_kwargs.kwargs.get("model", call_kwargs.args[0] if call_kwargs.args else "").lower()
    assert "sonnet" in model_value
    assert "haiku" not in model_value


# ---------------------------------------------------------------------------
# Test 2: explore falls back to keyword matching when embedding raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_fallback_on_no_embedding(tmp_path):
    loader = _make_prompt_loader(tmp_path)
    gc = _make_github_client(
        tree_items=[
            {"type": "blob", "path": "src/main.py", "sha": "f1", "size": 100},
            {"type": "blob", "path": "src/utils.py", "sha": "f2", "size": 200},
            {"type": "blob", "path": "docs/readme.md", "sha": "f3", "size": 50},
        ]
    )
    es = _make_embedding_service(should_raise=True)
    provider = _make_provider(context_text="Fallback context for main.py")

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    result = await explorer.explore(
        raw_prompt="Write a main function",
        repo_full_name="owner/repo",
        branch="main",
        token="ghp_test",
    )

    # Should still return a result via keyword fallback
    assert result is not None
    assert isinstance(result, str)

    # Provider should still have been called (synthesis still runs)
    provider.complete_parsed.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 3: explore respects file limit (EXPLORE_MAX_FILES)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_respects_file_limit(tmp_path):
    loader = _make_prompt_loader(tmp_path)

    # Create 100 files in tree
    tree_items = [
        {"type": "blob", "path": f"src/file_{i}.py", "sha": f"sha{i}", "size": 100}
        for i in range(100)
    ]
    gc = _make_github_client(tree_items=tree_items)

    # Make cosine_search return top-100 results (more than limit)
    es = _make_embedding_service()
    es.cosine_search.return_value = [(i, 0.99 - i * 0.005) for i in range(100)]

    provider = _make_provider()

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    with patch("app.services.codebase_explorer.settings") as mock_settings:
        mock_settings.EXPLORE_MAX_FILES = 40
        mock_settings.EXPLORE_TOTAL_LINE_BUDGET = 15000
        mock_settings.EXPLORE_MAX_CONTEXT_CHARS = 700000
        mock_settings.EXPLORE_MAX_PROMPT_CHARS = 20000

        result = await explorer.explore(
            raw_prompt="Refactor the codebase",
            repo_full_name="owner/repo",
            branch="main",
            token="ghp_test",
        )

    assert result is not None

    # Verify file reads were capped at 40
    assert gc.get_file_content.await_count <= 40


# ---------------------------------------------------------------------------
# Test 4: explore caches results — second call with same SHA skips provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_uses_cache(tmp_path):
    """Second call with same SHA returns cached result without calling provider."""
    from app.services.codebase_explorer import ExploreOutput, _explore_cache

    # Clear cache to avoid test pollution
    _explore_cache._store.clear()

    loader = _make_prompt_loader(tmp_path)

    gc = AsyncMock()
    gc.get_branch_head_sha = AsyncMock(return_value="sha1")
    gc.get_tree = AsyncMock(return_value=[
        {"path": "src/main.py", "type": "blob", "sha": "a1", "size": 100},
    ])
    gc.get_file_content = AsyncMock(return_value="def main(): pass")

    es = MagicMock()
    es.aembed_single = AsyncMock(return_value=np.zeros(384))
    es.aembed_texts = AsyncMock(return_value=[np.zeros(384)])
    es.cosine_search = MagicMock(return_value=[(0, 0.9)])

    provider = AsyncMock()
    provider.complete_parsed = AsyncMock(
        return_value=ExploreOutput(context="Synthesized context")
    )

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    # First call — cache miss
    result1 = await explorer.explore("Write a function", "owner/repo", "main", "token")
    assert result1 == "Synthesized context"
    assert provider.complete_parsed.call_count == 1

    # Second call — cache hit (same SHA + prompt)
    result2 = await explorer.explore("Write a function", "owner/repo", "main", "token")
    assert result2 == "Synthesized context"
    # Provider should NOT have been called again
    assert provider.complete_parsed.call_count == 1

    # Third call with different prompt — cache miss
    result3 = await explorer.explore("Write a different thing", "owner/repo", "main", "token")
    assert result3 == "Synthesized context"
    assert provider.complete_parsed.call_count == 2


# ---------------------------------------------------------------------------
# Test 5: explore logs per-call budget utilization before the LLM call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_logs_budget_utilization(tmp_path, caplog):
    """Per-call utilization log surfaces chars/cap ratio + file count.

    Why: we're tuned to the empirical ~60K-token effective ceiling imposed
    by the CLI baseline.  A per-call log lets us confirm live utilization
    against the cap instead of guessing from synthesis_chars after the fact.
    """
    from app.providers.base import TokenUsage
    from app.services.codebase_explorer import _explore_cache

    _explore_cache._store.clear()

    loader = _make_prompt_loader(tmp_path)
    gc = _make_github_client(
        tree_items=[
            {"type": "blob", "path": f"src/f{i}.py", "sha": f"s{i}", "size": 500}
            for i in range(5)
        ],
        file_contents="def fn():\n    return 42\n",
    )
    es = _make_embedding_service()
    es.aembed_single = AsyncMock(return_value=np.zeros(384, dtype=np.float32))
    es.aembed_texts = AsyncMock(
        return_value=[np.zeros(384, dtype=np.float32) for _ in range(5)]
    )
    es.cosine_search.return_value = [(i, 0.9 - i * 0.01) for i in range(5)]

    provider = _make_provider()
    # Simulate CLI baseline (~140K tokens cached) so the log reflects it.
    provider.last_usage = TokenUsage(
        input_tokens=3_000,
        output_tokens=400,
        cache_read_tokens=140_000,
        cache_creation_tokens=0,
    )

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    with caplog.at_level("INFO", logger="app.services.codebase_explorer"):
        result = await explorer.explore(
            raw_prompt="Describe the codebase",
            repo_full_name="owner/repo",
            branch="main",
            token="ghp_test",
        )

    assert result is not None

    messages = [rec.getMessage() for rec in caplog.records]

    # Pre-LLM utilization line: must expose payload chars, cap, and ratio.
    budget_line = next(
        (m for m in messages if m.startswith("explore_budget:")), None
    )
    assert budget_line is not None, (
        "expected 'explore_budget: ...' log line before LLM call, got: "
        + "\n".join(messages)
    )
    assert "files=" in budget_line
    assert "payload_chars=" in budget_line
    assert "cap=" in budget_line
    assert "utilization=" in budget_line
    assert "%" in budget_line

    # Post-synthesis line: must surface provider cache_read so CLI baseline
    # (~140K tokens) is visible against Haiku's 200K window.
    synth_line = next(
        (m for m in messages if m.startswith("explore_synthesis:")), None
    )
    assert synth_line is not None
    assert "cache_read_tokens=140000" in synth_line


# ---------------------------------------------------------------------------
# Test 6: explore emits a JSONL trace entry after synthesis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_emits_trace_entry(tmp_path):
    """Each explore run writes one trace_id='explore:repo@branch@sha' entry.

    Lets operators audit per-call Haiku/Sonnet cost + latency + synthesis
    output size from `data/traces/traces-*.jsonl` instead of log scraping.
    """
    from app.providers.base import TokenUsage
    from app.services.codebase_explorer import _explore_cache
    from app.services.trace_logger import TraceLogger

    _explore_cache._store.clear()
    traces_dir = tmp_path / "traces"

    loader = _make_prompt_loader(tmp_path)
    gc = _make_github_client(
        tree_items=[
            {"type": "blob", "path": "src/main.py", "sha": "f1", "size": 100},
            {"type": "blob", "path": "src/utils.py", "sha": "f2", "size": 200},
        ]
    )
    es = _make_embedding_service()
    es.aembed_single = AsyncMock(return_value=np.zeros(384, dtype=np.float32))
    es.aembed_texts = AsyncMock(
        return_value=[np.zeros(384, dtype=np.float32) for _ in range(2)]
    )
    provider = _make_provider(context_text="Synthesized overview")
    provider.last_usage = TokenUsage(
        input_tokens=1_500, output_tokens=300,
        cache_read_tokens=120_000, cache_creation_tokens=0,
    )

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    with patch(
        "app.services.codebase_explorer._get_trace_logger",
        return_value=TraceLogger(traces_dir),
    ):
        result = await explorer.explore(
            raw_prompt="Describe architecture",
            repo_full_name="owner/repo",
            branch="main",
            token="ghp_test",
        )

    assert result is not None

    entries = TraceLogger(traces_dir).read_trace("explore:owner/repo@main@sha_abc1")
    assert len(entries) == 1, f"expected 1 explore trace, got {len(entries)}"
    entry = entries[0]
    assert entry["phase"] == "explore_synthesis"
    assert entry["status"] == "ok"
    assert "sonnet" in entry["model"].lower()
    assert entry["tokens_in"] == 1_500
    assert entry["tokens_out"] == 300
    assert entry["result"]["repo"] == "owner/repo"
    assert entry["result"]["branch"] == "main"
    assert entry["result"]["files_read"] == 2
    assert entry["result"]["synthesis_chars"] > 0
    assert entry["result"]["cache_read_tokens"] == 120_000


@pytest.mark.asyncio
async def test_explore_trace_failure_does_not_break_synthesis(tmp_path):
    """A failing TraceLogger.log_phase must not break the synthesis result."""
    from unittest.mock import MagicMock

    from app.services.codebase_explorer import _explore_cache

    _explore_cache._store.clear()

    loader = _make_prompt_loader(tmp_path)
    gc = _make_github_client()
    es = _make_embedding_service()
    es.aembed_single = AsyncMock(return_value=np.zeros(384, dtype=np.float32))
    es.aembed_texts = AsyncMock(
        return_value=[np.zeros(384, dtype=np.float32) for _ in range(2)]
    )
    provider = _make_provider()

    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=gc,
        embedding_service=es,
        provider=provider,
    )

    broken_logger = MagicMock()
    broken_logger.log_phase.side_effect = RuntimeError("disk full")

    with patch(
        "app.services.codebase_explorer._get_trace_logger",
        return_value=broken_logger,
    ):
        result = await explorer.explore(
            raw_prompt="Describe architecture",
            repo_full_name="owner/repo",
            branch="main",
            token="ghp_test",
        )

    # Synthesis succeeded despite trace failure.
    assert result is not None
    assert isinstance(result, str)
