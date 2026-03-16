# MCP Roots Scanning — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an MCP client connects, scan workspace roots for agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.) and inject the concatenated content as `codebase_guidance` into the optimization pipeline — giving MCP passthrough users codebase-aware optimization for free.

**Architecture:** New `RootsScanner` service discovers and reads guidance files from filesystem paths. The context resolver gains a `scan_roots()` class method that accepts root paths and returns concatenated guidance text (per-file capped at 500 lines / 10K chars, total capped at MAX_GUIDANCE_CHARS). The MCP `synthesis_prepare_optimization` tool passes `workspace_path` to the scanner. The optimize router gains an optional `workspace_path` parameter for REST API callers.

**Tech Stack:** Python 3.12+, pathlib, existing PromptLoader/ContextResolver

**Spec Reference:** Section 3 (Context Injection System — MCP Roots Scanning)

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `backend/app/services/roots_scanner.py` | Discover + read guidance files from workspace roots |
| `backend/tests/test_roots_scanner.py` | Scanner tests |

### Modify

| File | Changes |
|------|---------|
| `backend/app/services/context_resolver.py` | Add `scan_roots()` method that calls RootsScanner |
| `backend/app/routers/optimize.py` | Accept optional `workspace_path`, pass guidance to pipeline |
| `backend/app/mcp_server.py` | Wire `workspace_path` → roots scanner → `codebase_guidance` in prepare + optimize tools |
| `backend/tests/test_context_resolver.py` | Add roots scanning tests |

---

## Chunk 1: Roots Scanner Service

### Task 1: RootsScanner Service

**Files:**
- Create: `backend/app/services/roots_scanner.py`
- Create: `backend/tests/test_roots_scanner.py`

- [ ] **Step 1: Write roots scanner tests**

```python
# backend/tests/test_roots_scanner.py
"""Tests for MCP workspace roots scanning."""

import pytest
from pathlib import Path
from app.services.roots_scanner import RootsScanner


@pytest.fixture
def workspace(tmp_path):
    """Create a mock workspace with guidance files."""
    (tmp_path / "CLAUDE.md").write_text("# Claude Guidance\nUse pytest for testing.\nFollow PEP 8.")
    (tmp_path / "AGENTS.md").write_text("# Agent Guidance\nUse structured output.")
    (tmp_path / ".cursorrules").write_text("Always use TypeScript strict mode.")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")  # not a guidance file
    return tmp_path


class TestRootsScanner:
    def test_discovers_guidance_files(self, workspace):
        scanner = RootsScanner()
        files = scanner.discover(workspace)
        names = [f.name for f in files]
        assert "CLAUDE.md" in names
        assert "AGENTS.md" in names
        assert ".cursorrules" in names
        assert "main.py" not in names

    def test_reads_and_concatenates(self, workspace):
        scanner = RootsScanner()
        result = scanner.scan(workspace)
        assert "Claude Guidance" in result
        assert "Agent Guidance" in result
        assert "TypeScript strict mode" in result

    def test_per_file_cap_lines(self, workspace):
        # Write a very long CLAUDE.md (600 lines, exceeding 500 line cap)
        (workspace / "CLAUDE.md").write_text("\n".join(f"line {i}" for i in range(600)))
        scanner = RootsScanner()
        result = scanner.scan(workspace)
        # Should be truncated — not all 600 lines present
        assert "line 499" in result
        assert "line 500" not in result

    def test_per_file_cap_chars(self, workspace):
        # Write a file exceeding 10K char cap
        (workspace / "CLAUDE.md").write_text("x" * 15000)
        scanner = RootsScanner()
        result = scanner.scan(workspace)
        # Each file section includes header + delimiters, so content < 10K
        # Check the CLAUDE.md section doesn't contain 15000 x's
        claude_section_start = result.find("CLAUDE.md")
        claude_content = result[claude_section_start:]
        assert len(claude_content) < 12000  # 10K content + header overhead

    def test_wraps_in_untrusted_context(self, workspace):
        scanner = RootsScanner()
        result = scanner.scan(workspace)
        assert '<untrusted-context source="CLAUDE.md">' in result
        assert "</untrusted-context>" in result

    def test_empty_workspace(self, tmp_path):
        scanner = RootsScanner()
        result = scanner.scan(tmp_path)
        assert result is None

    def test_nonexistent_path(self):
        scanner = RootsScanner()
        result = scanner.scan(Path("/nonexistent/path"))
        assert result is None

    def test_github_copilot_instructions(self, workspace):
        """Discovers .github/copilot-instructions.md."""
        gh_dir = workspace / ".github"
        gh_dir.mkdir()
        (gh_dir / "copilot-instructions.md").write_text("Copilot rules here.")
        scanner = RootsScanner()
        result = scanner.scan(workspace)
        assert "Copilot rules here" in result

    def test_windsurfrules(self, workspace):
        (workspace / ".windsurfrules").write_text("Windsurf rules.")
        scanner = RootsScanner()
        result = scanner.scan(workspace)
        assert "Windsurf rules" in result

    def test_total_output_capped(self, workspace):
        """Total concatenated output respects MAX_GUIDANCE_CHARS."""
        # Write large files that together exceed 20K
        for name in ["CLAUDE.md", "AGENTS.md", ".cursorrules"]:
            (workspace / name).write_text("a" * 9000)
        scanner = RootsScanner(max_total_chars=20000)
        result = scanner.scan(workspace)
        assert len(result) <= 22000  # 20K content + XML wrapper overhead
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_roots_scanner.py -v`

- [ ] **Step 3: Implement RootsScanner**

```python
# backend/app/services/roots_scanner.py
"""Scan workspace roots for agent guidance files.

Discovers CLAUDE.md, AGENTS.md, .cursorrules, .github/copilot-instructions.md,
and .windsurfrules in workspace directories. Each file is capped at 500 lines /
10K chars, wrapped in <untrusted-context> delimiters, and concatenated.
"""

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Files to scan, in discovery order
GUIDANCE_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    ".windsurfrules",
]

MAX_LINES_PER_FILE = 500
MAX_CHARS_PER_FILE = 10_000


class RootsScanner:
    """Discovers and reads agent guidance files from workspace directories."""

    def __init__(self, max_total_chars: int | None = None) -> None:
        self._max_total = max_total_chars or settings.MAX_GUIDANCE_CHARS

    def discover(self, root: Path) -> list[Path]:
        """Return list of existing guidance file paths in the root."""
        found = []
        for rel_path in GUIDANCE_FILES:
            path = root / rel_path
            if path.is_file():
                found.append(path)
        return found

    def scan(self, root: Path) -> str | None:
        """Scan a workspace root and return concatenated guidance text.

        Returns None if no guidance files found or root doesn't exist.
        Each file is capped at 500 lines / 10K chars and wrapped in
        <untrusted-context source="filename"> delimiters.
        """
        if not root.exists() or not root.is_dir():
            return None

        files = self.discover(root)
        if not files:
            return None

        sections = []
        total_chars = 0

        for path in files:
            try:
                content = path.read_text(errors="replace")
            except OSError:
                logger.warning("Failed to read guidance file: %s", path)
                continue

            # Per-file caps
            lines = content.split("\n")
            if len(lines) > MAX_LINES_PER_FILE:
                content = "\n".join(lines[:MAX_LINES_PER_FILE])
            if len(content) > MAX_CHARS_PER_FILE:
                content = content[:MAX_CHARS_PER_FILE]

            # Check total budget
            if total_chars + len(content) > self._max_total:
                remaining = self._max_total - total_chars
                if remaining <= 0:
                    break
                content = content[:remaining]

            name = path.name if path.parent == path.parents[0] or path.parent.name != ".github" else f".github/{path.name}"
            section = (
                f'<untrusted-context source="{name}">\n'
                f"{content}\n"
                f"</untrusted-context>"
            )
            sections.append(section)
            total_chars += len(content)

        if not sections:
            return None

        return "\n\n".join(sections)

    def scan_roots(self, roots: list[Path]) -> str | None:
        """Scan multiple workspace roots and merge results."""
        all_sections = []
        for root in roots:
            result = self.scan(root)
            if result:
                all_sections.append(result)
        return "\n\n".join(all_sections) if all_sections else None
```

- [ ] **Step 4: Run tests — verify they pass**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/roots_scanner.py tests/test_roots_scanner.py
git commit -m "feat: implement MCP roots scanner for agent guidance file discovery"
```

---

## Chunk 2: Integration

### Task 2: Wire into Context Resolver

**Files:**
- Modify: `backend/app/services/context_resolver.py`
- Modify: `backend/tests/test_context_resolver.py`

- [ ] **Step 1: Add `scan_workspace` to ContextResolver**

Add a new class method that uses RootsScanner:

```python
@classmethod
def scan_workspace(cls, workspace_path: str | None) -> str | None:
    """Scan a workspace path for guidance files. Returns formatted guidance or None."""
    if not workspace_path:
        return None
    from app.services.roots_scanner import RootsScanner
    scanner = RootsScanner()
    return scanner.scan(Path(workspace_path))
```

Also update `resolve()` to accept `workspace_path` parameter. If `codebase_guidance` is None but `workspace_path` is provided, auto-scan:

```python
@staticmethod
def resolve(
    raw_prompt: str,
    strategy_override: str | None = None,
    codebase_guidance: str | None = None,
    codebase_context: str | None = None,
    adaptation_state: str | None = None,
    workspace_path: str | None = None,  # NEW
) -> ResolvedContext:
    # ... existing validation ...

    # Auto-scan workspace if no explicit guidance provided
    if codebase_guidance is None and workspace_path:
        from app.services.roots_scanner import RootsScanner
        scanner = RootsScanner()
        scanned = scanner.scan(Path(workspace_path))
        if scanned:
            codebase_guidance = scanned

    # ... existing truncation + wrapping (skip wrapping if already wrapped by scanner) ...
```

Important: The RootsScanner already wraps each file in `<untrusted-context>` tags. The ContextResolver should NOT double-wrap. Add a check: if `codebase_guidance` already contains `<untrusted-context`, skip the wrapping step.

- [ ] **Step 2: Add tests for workspace scanning integration**

```python
class TestWorkspaceScanning:
    def test_resolve_with_workspace_path(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Project rules\nUse pytest.")
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            workspace_path=str(tmp_path),
        )
        assert ctx.codebase_guidance is not None
        assert "Project rules" in ctx.codebase_guidance
        assert ctx.context_sources["codebase_guidance"] is True

    def test_explicit_guidance_takes_precedence(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("From workspace")
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            codebase_guidance="Explicit guidance",
            workspace_path=str(tmp_path),
        )
        # Explicit guidance should win — workspace not scanned
        assert "Explicit guidance" in ctx.codebase_guidance
        assert "From workspace" not in ctx.codebase_guidance

    def test_workspace_no_guidance_files(self, tmp_path):
        ctx = ContextResolver.resolve(
            raw_prompt="Write a function that sorts a list",
            workspace_path=str(tmp_path),
        )
        assert ctx.codebase_guidance is None
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
cd backend && git add app/services/context_resolver.py tests/test_context_resolver.py
git commit -m "feat: integrate roots scanner into context resolver"
```

---

### Task 3: Wire into Optimize Router + MCP Server

**Files:**
- Modify: `backend/app/routers/optimize.py`
- Modify: `backend/app/mcp_server.py`

- [ ] **Step 1: Add workspace_path to OptimizeRequest**

```python
class OptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=20)
    strategy: str | None = None
    workspace_path: str | None = Field(None, description="Workspace root for guidance file scanning")
```

Pass it through to the pipeline:
```python
orchestrator.run(
    raw_prompt=body.prompt, provider=provider, db=db,
    strategy_override=body.strategy,
    workspace_path=body.workspace_path,  # NEW
)
```

But wait — the pipeline's `run()` doesn't accept `workspace_path`. The scanning should happen at the router level via ContextResolver, then pass `codebase_guidance` to the pipeline:

```python
# In the optimize endpoint, before calling pipeline:
from app.services.context_resolver import ContextResolver
guidance = ContextResolver.scan_workspace(body.workspace_path)

orchestrator.run(
    raw_prompt=body.prompt, provider=provider, db=db,
    strategy_override=body.strategy,
    codebase_guidance=guidance,
)
```

- [ ] **Step 2: Wire workspace_path in MCP synthesis_prepare_optimization**

The `synthesis_prepare_optimization` tool already accepts `workspace_path` parameter but passes `None` for `codebase_guidance`. Wire it:

```python
# In synthesis_prepare_optimization, before rendering passthrough.md:
from app.services.roots_scanner import RootsScanner
guidance = None
if workspace_path:
    scanner = RootsScanner()
    guidance = scanner.scan(Path(workspace_path))

assembled = loader.render("passthrough.md", {
    "raw_prompt": prompt,
    "strategy_instructions": strategy_instructions,
    "scoring_rubric_excerpt": scoring_excerpt,
    "codebase_guidance": guidance,  # was None, now scanned
    ...
})
```

- [ ] **Step 3: Wire workspace_path in MCP synthesis_optimize**

Similar — scan roots before calling pipeline:

```python
# In synthesis_optimize:
from app.services.roots_scanner import RootsScanner
guidance = None
if workspace_path:
    scanner = RootsScanner()
    guidance = scanner.scan(Path(workspace_path)) if workspace_path else None

orchestrator.run(
    raw_prompt=prompt, provider=provider, db=db,
    strategy_override=strategy,
    codebase_guidance=guidance,
)
```

Note: `synthesis_optimize` already has a `workspace_path`... actually, looking at the current code, it doesn't — it only has `repo_full_name`. Add `workspace_path: str | None = None` parameter.

- [ ] **Step 4: Run full test suite**

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/routers/optimize.py app/mcp_server.py
git commit -m "feat: wire roots scanning into optimize router and MCP tools"
```

---

### Task 4: Verify + Final Tests

- [ ] **Step 1: Run full backend suite with coverage**

```bash
cd backend && source .venv/bin/activate && pytest --cov=app -v
```

- [ ] **Step 2: Verify frontend still builds**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit any test fixes**
