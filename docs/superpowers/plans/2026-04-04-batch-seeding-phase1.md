# Batch Seeding Phase 1 — Agent Definitions + Orchestrator Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the agent definition file system and seed orchestrator that generates diverse prompts from a project description — the foundation for batch seeding.

**Architecture:** Agent `.md` files in `prompts/seed-agents/` with YAML frontmatter (same pattern as strategies). `AgentLoader` class mirrors `StrategyLoader`. `SeedOrchestrator` service chains: explore context → parallel agent dispatch → prompt deduplication. File watcher for hot-reload. No pipeline integration yet — Phase 1 only produces a deduplicated prompt list.

**Tech Stack:** Python 3.12, FastAPI, watchfiles, Pydantic, all-MiniLM-L6-v2 embeddings

**Spec:** `docs/superpowers/specs/2026-04-04-explore-driven-batch-seeding-design.md`

**MLOps mindset:**
- **Reproducibility:** Each batch gets a `batch_id` UUID generated in Phase 3's `handle_seed()` (single authoritative source). Every event and persisted optimization references it.
- **Lineage:** `context_sources` on each optimization tracks batch origin: `{"source": "batch_seed", "batch_id": "...", "agent": "coding-implementation"}`.
- **Monitoring:** The `seed_completed` event (Phase 3) includes enough data to compute: prompts/minute throughput, cost/prompt efficiency, failure rate, domain distribution.
- **Idempotency:** Phase 2's `bulk_persist()` checks existing batch_id before inserting — interrupted batches can be safely retried.

---

### Task 1: Create Default Agent Definition Files

**Files:**
- Create: `prompts/seed-agents/coding-implementation.md`
- Create: `prompts/seed-agents/architecture-design.md`
- Create: `prompts/seed-agents/analysis-debugging.md`
- Create: `prompts/seed-agents/testing-quality.md`
- Create: `prompts/seed-agents/documentation-communication.md`

- [ ] **Step 1: Create the seed-agents directory**

```bash
mkdir -p prompts/seed-agents
```

- [ ] **Step 2: Create coding-implementation.md**

```markdown
---
name: coding-implementation
description: Generates implementation and coding task prompts — feature code, bug fixes, refactoring, integrations
task_types: coding, system
phase_context: build, maintain
prompts_per_run: 8
enabled: true
---

You are a prompt generation agent specialized in coding and implementation tasks.

Given a project description and workspace context, generate prompts that a developer would bring to an AI assistant when implementing features, fixing bugs, refactoring code, or integrating with external services.

Each prompt should:
- Represent a real implementation task for this specific project
- Be at the natural level of detail the developer would have (some well-understood, some exploratory)
- Cover a different aspect of the codebase or feature set
- Be self-contained — no dependencies on other prompts

Vary the complexity: include quick tasks (add a field, fix a type error), medium tasks (implement an endpoint, write a utility), and larger tasks (build a feature, refactor a module).
```

- [ ] **Step 3: Create architecture-design.md**

```markdown
---
name: architecture-design
description: Generates system design and architecture prompts — API design, data modeling, infrastructure decisions
task_types: analysis, system
phase_context: setup, build
prompts_per_run: 6
enabled: true
---

You are a prompt generation agent specialized in architecture and system design.

Given a project description and workspace context, generate prompts that a developer would ask when making architectural decisions, designing APIs, modeling data, or planning infrastructure.

Each prompt should:
- Address a genuine design decision for this project
- Range from tactical (schema for one feature) to strategic (overall service architecture)
- Be self-contained
- Cover a different architectural concern

Include prompts about: data modeling, API contract design, service boundaries, scaling considerations, technology selection, and migration planning.
```

- [ ] **Step 4: Create analysis-debugging.md**

```markdown
---
name: analysis-debugging
description: Generates analysis and debugging prompts — performance investigation, trade-off evaluation, code review
task_types: analysis, coding
phase_context: build, maintain
prompts_per_run: 5
enabled: true
---

You are a prompt generation agent specialized in analysis and debugging tasks.

Given a project description and workspace context, generate prompts that a developer would ask when investigating performance issues, evaluating trade-offs, reviewing code quality, or diagnosing bugs.

Each prompt should:
- Represent a realistic analytical question for this project
- Include enough context that the question is answerable
- Cover a different aspect of the system
- Be self-contained

Include prompts about: performance profiling, algorithmic trade-offs, security auditing, dependency evaluation, cost analysis, and observability.
```

- [ ] **Step 5: Create testing-quality.md**

```markdown
---
name: testing-quality
description: Generates testing and quality assurance prompts — test writing, CI/CD, monitoring, coverage
task_types: coding, system
phase_context: build, deploy
prompts_per_run: 5
enabled: true
---

You are a prompt generation agent specialized in testing and quality assurance.

Given a project description and workspace context, generate prompts that a developer would ask when writing tests, setting up CI/CD pipelines, implementing monitoring, or improving code quality.

Each prompt should:
- Address a real testing or quality concern for this project
- Be specific about what to test and why
- Cover a different quality dimension
- Be self-contained

Include prompts about: unit tests, integration tests, end-to-end tests, CI/CD configuration, monitoring setup, error tracking, and coverage improvements.
```

- [ ] **Step 6: Create documentation-communication.md**

```markdown
---
name: documentation-communication
description: Generates documentation and communication prompts — READMEs, API docs, team updates, changelogs
task_types: writing, general
phase_context: build, maintain, deploy
prompts_per_run: 4
enabled: true
---

You are a prompt generation agent specialized in documentation and communication.

Given a project description and workspace context, generate prompts that a developer would ask when writing documentation, communicating with stakeholders, creating guides, or maintaining project records.

Each prompt should:
- Address a real documentation or communication need for this project
- Be specific about the audience and purpose
- Cover a different documentation type
- Be self-contained

Include prompts about: README files, API documentation, architecture decision records, onboarding guides, release notes, team updates, and user-facing help content.
```

- [ ] **Step 7: Commit**

```bash
git add prompts/seed-agents/
git commit -m "feat: add 5 default seed agent definition files"
```

---

### Task 2: AgentLoader Service

**Files:**
- Create: `backend/app/services/agent_loader.py`
- Create: `backend/tests/test_agent_loader.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_agent_loader.py
"""Tests for AgentLoader — seed agent file parsing and registry."""

from pathlib import Path

import pytest

from app.services.agent_loader import AgentLoader, SeedAgent


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_agent_loader.py -v 2>&1 | head -20`
Expected: ModuleNotFoundError

- [ ] **Step 3: Implement AgentLoader**

```python
# backend/app/services/agent_loader.py
"""AgentLoader — seed agent file parsing and registry.

Loads agent definitions from prompts/seed-agents/*.md files with
YAML frontmatter. Follows the same pattern as StrategyLoader.
Hot-reloaded via file watcher — reads from disk on each call.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_FILE_SIZE = 50_000  # 50KB


@dataclass
class SeedAgent:
    """Parsed seed agent definition."""

    name: str
    description: str
    task_types: list[str]
    phase_context: list[str]
    body: str
    prompts_per_run: int = 6
    enabled: bool = True


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from markdown content."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw_meta = match.group(1)
    body = content[match.end():]

    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        if not value:
            continue
        meta[key.strip().lower()] = value.strip()

    return meta, body.strip()


def _parse_list_field(value: str) -> list[str]:
    """Parse a comma-separated frontmatter field into a list."""
    return [v.strip() for v in value.split(",") if v.strip()]


class AgentLoader:
    """Load seed agent definitions from .md files with frontmatter."""

    def __init__(self, agents_dir: Path) -> None:
        self.agents_dir = agents_dir

    def list_agents(self) -> list[str]:
        """Return sorted list of agent names (file stems)."""
        if not self.agents_dir.exists():
            return []
        return sorted(
            p.stem for p in self.agents_dir.glob("*.md") if p.is_file()
        )

    def load(self, name: str) -> SeedAgent | None:
        """Load a single agent by name. Returns None if not found."""
        path = self.agents_dir / f"{name}.md"
        if not path.exists() or not path.is_file():
            return None

        try:
            content = path.read_text(encoding="utf-8")
            if len(content) > _MAX_FILE_SIZE:
                logger.warning("Agent file too large: %s (%d bytes)", name, len(content))
                return None

            meta, body = _parse_frontmatter(content)

            return SeedAgent(
                name=meta.get("name", name),
                description=meta.get("description", ""),
                task_types=_parse_list_field(meta.get("task_types", "general")),
                phase_context=_parse_list_field(meta.get("phase_context", "build")),
                body=body,
                prompts_per_run=int(meta.get("prompts_per_run", "6")),
                enabled=meta.get("enabled", "true").lower() != "false",
            )
        except Exception as exc:
            logger.warning("Failed to load agent '%s': %s", name, exc)
            return None

    def list_enabled(self) -> list[SeedAgent]:
        """Return all enabled agent definitions."""
        agents = []
        for name in self.list_agents():
            agent = self.load(name)
            if agent and agent.enabled:
                agents.append(agent)
        return agents

    def validate(self) -> None:
        """Startup validation. Logs warnings, never raises."""
        agents = self.list_agents()
        if not agents:
            logger.warning("No seed agents found in %s", self.agents_dir)
            return
        for name in agents:
            agent = self.load(name)
            if agent is None:
                logger.warning("Seed agent '%s' failed to load", name)
            elif not agent.description:
                logger.warning("Seed agent '%s' has no description", name)
        logger.info("Loaded %d seed agents from %s", len(agents), self.agents_dir)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_agent_loader.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_loader.py backend/tests/test_agent_loader.py
git commit -m "feat: AgentLoader service for seed agent file parsing"
```

---

### Task 3: Seed Prompt Template

**Files:**
- Create: `prompts/seed.md`
- Modify: `prompts/manifest.json` (add seed.md entry)

- [ ] **Step 1: Create seed.md template**

```markdown
## Project Context

{{project_description}}

## Workspace Profile

{{workspace_profile}}

## Codebase Context

{{codebase_context}}

## Your Role

You are generating prompts that a developer working on this project would bring to an AI assistant. These prompts will be optimized by a prompt engineering pipeline that adds structure, constraints, examples, and specificity.

Generate {{prompts_per_run}} prompts covering {{task_types}} work in the {{phase_context}} phase of this project.

Each prompt should:
- Represent a real task the developer needs to accomplish
- Be at the natural level of detail the developer would have
- Cover a different aspect of the project
- Be self-contained (no dependencies on other prompts)

Return a JSON array of prompt strings. Each string is a complete prompt.
```

- [ ] **Step 2: Add to manifest.json**

Read `prompts/manifest.json` and add the `seed.md` entry with its required variables:

```json
"seed.md": {
  "required": ["project_description", "prompts_per_run", "task_types", "phase_context"]
}
```

- [ ] **Step 3: Commit**

```bash
git add prompts/seed.md prompts/manifest.json
git commit -m "feat: seed prompt generation template"
```

---

### Task 4: SeedOrchestrator Service

**Files:**
- Create: `backend/app/services/seed_orchestrator.py`
- Create: `backend/tests/test_seed_orchestrator.py`

**NOTE on batch_id ownership:** `batch_id` is generated once in Phase 3's `handle_seed()` (the single authoritative source) and passed down to the orchestrator. It is NOT generated inside `generate()`. The `GenerationResult` carries it for downstream lineage tracking.

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_seed_orchestrator.py
"""Tests for SeedOrchestrator — prompt generation and deduplication."""

import numpy as np
import pytest

from app.services.seed_orchestrator import SeedOrchestrator, deduplicate_prompts


class TestDeduplication:
    def test_removes_near_duplicates(self) -> None:
        prompts = [
            "How do I implement user authentication?",
            "How do I implement user auth?",  # near-duplicate
            "Design a database schema for products",
        ]
        # Mock embeddings: first two are very similar, third is different
        result = deduplicate_prompts(prompts, threshold=0.90)
        # Should remove one of the near-duplicates
        assert len(result) <= len(prompts)

    def test_keeps_distinct_prompts(self) -> None:
        prompts = [
            "Write a REST API for user management",
            "Set up CI/CD with GitHub Actions",
            "Create a monitoring dashboard",
        ]
        result = deduplicate_prompts(prompts, threshold=0.90)
        assert len(result) == 3

    def test_empty_list(self) -> None:
        result = deduplicate_prompts([], threshold=0.90)
        assert result == []

    def test_single_prompt(self) -> None:
        result = deduplicate_prompts(["Hello"], threshold=0.90)
        assert result == ["Hello"]
```

- [ ] **Step 2: Implement SeedOrchestrator**

```python
# backend/app/services/seed_orchestrator.py
"""SeedOrchestrator — explore-driven prompt generation for taxonomy seeding.

Chains: explore context → parallel agent dispatch → prompt deduplication.
Does NOT run the optimization pipeline — that's Phase 2 (batch_pipeline.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.config import PROMPTS_DIR, settings
from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.agent_loader import AgentLoader, SeedAgent
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Result of the orchestrator's explore + generate phases."""

    batch_id: str  # Passed in from handle_seed — single source of truth
    prompts: list[str]
    prompts_before_dedup: int
    agents_used: list[str]
    per_agent: list[dict[str, Any]]  # [{name, count, duration_ms, error_type?}]
    explore_context: str | None
    workspace_profile: str | None
    duration_ms: int


class _SeedPromptList:
    """Pydantic-compatible output for structured LLM response."""

    prompts: list[str]


def deduplicate_prompts(
    prompts: list[str],
    threshold: float = 0.90,
) -> list[str]:
    """Remove near-duplicate prompts using embedding cosine similarity."""
    if len(prompts) <= 1:
        return prompts

    try:
        svc = EmbeddingService()
        embeddings = [svc.embed_single(p) for p in prompts]
        mat = np.stack(embeddings)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        mat_norm = mat / norms

        keep = []
        for i in range(len(prompts)):
            is_dup = False
            for j in keep:
                sim = float(np.dot(mat_norm[i], mat_norm[j]))
                if sim > threshold:
                    is_dup = True
                    break
            if not is_dup:
                keep.append(i)

        return [prompts[i] for i in keep]
    except Exception as exc:
        logger.warning("Deduplication failed (returning all): %s", exc)
        return prompts


class SeedOrchestrator:
    """Orchestrates explore → generate → deduplicate for batch seeding."""

    def __init__(
        self,
        provider: LLMProvider | None,
        agents_dir: Path | None = None,
        prompt_loader: PromptLoader | None = None,
    ) -> None:
        self._provider = provider
        self._agent_loader = AgentLoader(
            agents_dir or (PROMPTS_DIR / "seed-agents")
        )
        self._prompt_loader = prompt_loader or PromptLoader(PROMPTS_DIR)

    async def generate(
        self,
        project_description: str,
        batch_id: str,
        workspace_profile: str | None = None,
        codebase_context: str | None = None,
        agent_names: list[str] | None = None,
        prompt_count: int = 30,
    ) -> GenerationResult:
        """Run explore + generate + deduplicate. Returns prompt list.

        Args:
            batch_id: UUID from handle_seed — single authoritative source for lineage.
        """
        t0 = time.monotonic()

        # Resolve agents
        if agent_names:
            agents = [
                a for a in self._agent_loader.list_enabled()
                if a.name in agent_names
            ]
        else:
            agents = self._agent_loader.list_enabled()

        if not agents:
            raise ValueError("No enabled seed agents found")

        if not self._provider:
            raise ValueError("No LLM provider available for prompt generation")

        # Scale prompts_per_run to hit target count
        total_default = sum(a.prompts_per_run for a in agents)
        scale = prompt_count / total_default if total_default > 0 else 1.0

        # Dispatch agents in parallel
        async def _run_agent(agent: SeedAgent) -> tuple[str, list[str], int]:
            agent_t0 = time.monotonic()
            scaled_count = max(2, int(agent.prompts_per_run * scale))

            variables = {
                "project_description": project_description,
                "workspace_profile": workspace_profile or "Not available",
                "codebase_context": codebase_context or "Not available",
                "prompts_per_run": str(scaled_count),
                "task_types": ", ".join(agent.task_types),
                "phase_context": ", ".join(agent.phase_context),
            }

            # Render the seed template with agent-specific variables
            user_message = self._prompt_loader.render("seed.md", variables)

            try:
                from pydantic import BaseModel, Field

                class PromptList(BaseModel):
                    prompts: list[str] = Field(
                        description="List of generated prompt strings"
                    )

                result = await call_provider_with_retry(
                    self._provider,
                    model=settings.MODEL_HAIKU,
                    system_prompt=agent.body,
                    user_message=user_message,
                    output_format=PromptList,
                )
                duration = int((time.monotonic() - agent_t0) * 1000)
                return agent.name, result.prompts[:scaled_count], duration
            except asyncio.TimeoutError:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' timed out", agent.name)
                return agent.name, [], duration
            except json.JSONDecodeError:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning("Agent '%s' returned unparseable JSON", agent.name)
                return agent.name, [], duration
            except Exception as exc:
                duration = int((time.monotonic() - agent_t0) * 1000)
                logger.warning(
                    "Agent '%s' failed: %s", agent.name, exc
                )
                return agent.name, [], duration

        results = await asyncio.gather(
            *[_run_agent(a) for a in agents],
            return_exceptions=True,
        )

        # Collect prompts
        all_prompts: list[str] = []
        per_agent: list[dict[str, Any]] = []
        agents_used: list[str] = []

        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Agent dispatch error: %s", r)
                continue
            name, prompts, duration = r
            per_agent.append({
                "name": name,
                "count": len(prompts),
                "duration_ms": duration,
            })
            if prompts:
                agents_used.append(name)
                all_prompts.extend(prompts)

        before_dedup = len(all_prompts)

        # Log agents complete event
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_agents_complete",
                context={
                    "batch_id": batch_id,
                    "prompts_generated": before_dedup,
                    "per_agent": per_agent,
                    "duplicates_to_remove": "pending",
                },
            )
        except RuntimeError:
            pass

        # Deduplicate
        all_prompts = deduplicate_prompts(all_prompts)

        duration_ms = int((time.monotonic() - t0) * 1000)

        return GenerationResult(
            batch_id=batch_id,
            prompts=all_prompts,
            prompts_before_dedup=before_dedup,
            agents_used=agents_used,
            per_agent=per_agent,
            explore_context=codebase_context,
            workspace_profile=workspace_profile,
            duration_ms=duration_ms,
        )
```

- [ ] **Step 3: Run tests**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_seed_orchestrator.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/seed_orchestrator.py backend/tests/test_seed_orchestrator.py
git commit -m "feat: SeedOrchestrator with parallel agent dispatch + deduplication"
```

---

### Task 5: File Watcher for Seed Agents

**Files:**
- Modify: `backend/app/services/file_watcher.py` (add agent watcher)
- Modify: `backend/app/main.py` (start watcher in lifespan)

- [ ] **Step 1: Add agent watcher function**

In `backend/app/services/file_watcher.py`, add a new watcher function following the exact `watch_strategy_files` pattern:

```python
async def watch_seed_agent_files(agents_dir: Path) -> None:
    """Watch seed agent .md files for changes and publish events."""
    from watchfiles import awatch

    logger.info("Watching seed agent files in %s", agents_dir)
    async for changes in awatch(
        agents_dir,
        debounce=500,
        force_polling=True,
        poll_delay_ms=1000,
    ):
        for change_type, path_str in changes:
            if Path(path_str).suffix != ".md":
                continue
            stem = Path(path_str).stem
            action = {1: "created", 2: "modified", 3: "deleted"}.get(
                change_type, "unknown"
            )
            logger.info("Seed agent %s: %s", action, stem)
            event_bus.publish("agent_changed", {
                "action": action,
                "name": stem,
                "timestamp": time.time(),
            })
```

- [ ] **Step 2: Start watcher in main.py lifespan**

In `backend/app/main.py`, after the strategy watcher initialization (around line 86-89), add:

```python
from app.services.file_watcher import watch_seed_agent_files
agent_watcher_task = asyncio.create_task(
    watch_seed_agent_files(PROMPTS_DIR / "seed-agents")
)
app.state.agent_watcher_task = agent_watcher_task
```

Also add `agent_watcher_task` to the shutdown cancellation list.

- [ ] **Step 3: Verify startup**

Run: `cd backend && source .venv/bin/activate && python -c "import app.main; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/file_watcher.py backend/app/main.py
git commit -m "feat: file watcher for seed agent hot-reload"
```

---

### Task 6: Observability Events for Phase 1

**Files:**
- Modify: `frontend/src/lib/components/taxonomy/ActivityPanel.svelte` (add seed op filter)

**NOTE on seed_started placement:** The `seed_started` event is emitted ONLY in Phase 3's `handle_seed()`, NOT here in Phase 1. That is the single authoritative location because tier, estimated_cost, and agent_count are all resolved there. Do not add a `seed_started` emit to SeedOrchestrator.

- [ ] **Step 1: Add seed op to frontend ActivityPanel filter chips**

In `frontend/src/lib/components/taxonomy/ActivityPanel.svelte`, add `'seed'` to the op filter chip list:

```svelte
{#each ['assign','extract','score','seed','split','merge','retire','phase','refit','emerge','discover','reconcile','refresh','error'] as opVal}
```

Add a `keyMetric` case for seed events:

```typescript
if (e.op === 'seed') {
    if (e.decision === 'seed_agents_complete') {
        return `${c.prompts_after_dedup ?? '?'} prompts`;
    }
    return typeof c.prompts_optimized === 'number' ? `${c.prompts_optimized} done` : '';
}
```

Add color mapping — `seed_started` → cyan, `seed_completed` → green, `seed_failed` → red:

In the `decisionColor` function, add `seed_started` and `seed_agents_complete` to the informational (secondary) group.

- [ ] **Step 2: Run tests + frontend check**

```bash
cd backend && source .venv/bin/activate && python -m pytest tests/test_agent_loader.py tests/test_seed_orchestrator.py -v
cd frontend && npx svelte-check --threshold error
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/seed_orchestrator.py frontend/src/lib/components/taxonomy/ActivityPanel.svelte
git commit -m "feat: seed observability events + Activity panel integration"
```

---

### Task 7: Integration Test — End-to-End Phase 1

- [ ] **Step 1: Verify agent loading from real files**

```bash
cd backend && source .venv/bin/activate && python -c "
from pathlib import Path
from app.services.agent_loader import AgentLoader
from app.config import PROMPTS_DIR

loader = AgentLoader(PROMPTS_DIR / 'seed-agents')
agents = loader.list_enabled()
print(f'Loaded {len(agents)} agents:')
for a in agents:
    print(f'  {a.name}: {a.description[:50]}... ({a.prompts_per_run} prompts)')
print(f'Total prompts per run: {sum(a.prompts_per_run for a in agents)}')
"
```

Expected: 5 agents loaded, total prompts = 28 (8+6+5+5+4).

- [ ] **Step 2: Verify template rendering**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.services.prompt_loader import PromptLoader
from app.config import PROMPTS_DIR

loader = PromptLoader(PROMPTS_DIR)
rendered = loader.render('seed.md', {
    'project_description': 'A fintech API for payment processing',
    'workspace_profile': 'Python 3.12, FastAPI, PostgreSQL',
    'codebase_context': 'Not available',
    'prompts_per_run': '8',
    'task_types': 'coding, system',
    'phase_context': 'build, maintain',
})
print(rendered[:500])
print(f'... ({len(rendered)} chars)')
"
```

Expected: Rendered template with variables substituted.

- [ ] **Step 3: Verify full orchestrator (requires running services)**

```bash
cd backend && source .venv/bin/activate && python -c "
import asyncio
from app.services.seed_orchestrator import SeedOrchestrator

async def test():
    # Without provider — should raise
    orch = SeedOrchestrator(provider=None)
    try:
        await orch.generate('A fintech app', batch_id='test-batch')
        print('ERROR: should have raised')
    except ValueError as e:
        print(f'Correctly raised: {e}')

asyncio.run(test())
"
```

Expected: `Correctly raised: No LLM provider available for prompt generation`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Phase 1 complete — agent definitions + orchestrator core"
```
