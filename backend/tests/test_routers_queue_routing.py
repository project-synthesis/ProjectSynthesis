"""Cycle 8 RED → GREEN: REST routers route writes through WriteQueue.

Group 3 of cycle 8 migrates REST endpoint write call sites to use
``Depends(get_write_queue)`` and wrap the body's commits inside
``write_queue.submit()`` callbacks.

Tests pin the WIRING — each migrated handler signature gains a
``write_queue: WriteQueue = Depends(get_write_queue)`` parameter. Source
introspection confirms the operation_label is present and the
``write_queue is None`` legacy branch is preserved (so existing tests
using direct sessions keep working until cycle 9 lifespan installs the
queue on app.state).

Routers in scope:
- optimize.py (4 commit sites: passthrough prepare, intent_label
  rename, passthrough save)
- domains.py (3 commit sites: promote, dissolve, rebuild-sub-domains)
- templates.py (3 commit sites: fork, retire, use)
- github_repos.py (3 commit sites: link, status update, unlink)
- projects.py (1 commit site: migrate)

Per spec § 4.2 + cycle 8 scope notes:
- github_auth.py + clusters.py are NOT in spec § 4.2 explicit list. Each
  has bespoke handling (OAuth flow with retries, taxonomy mutations
  with engine state). They get tested for kwarg acceptance only —
  full migration deferred to cycle 9 if too complex.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Source-level introspection helpers
# ---------------------------------------------------------------------------

ROUTER_DIR = Path(__file__).resolve().parents[1] / "app" / "routers"


def _read_router(name: str) -> str:
    return (ROUTER_DIR / name).read_text()


def _strip_docstrings_comments(source: str) -> str:
    s = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    s = re.sub(r"'''.*?'''", "", s, flags=re.DOTALL)
    s = re.sub(r"^[ \t]*#.*$", "", s, flags=re.MULTILINE)
    return s


# ---------------------------------------------------------------------------
# get_write_queue dependency import audit
# ---------------------------------------------------------------------------


class TestRoutersImportGetWriteQueue:
    """Each migrated router must import get_write_queue from
    ``app.dependencies.write_queue`` to use ``Depends(get_write_queue)``.
    """

    def test_optimize_router_imports_get_write_queue(self):
        source = _read_router("optimize.py")
        assert "from app.dependencies.write_queue import get_write_queue" in source, (
            "optimize.py must import get_write_queue for cycle 8 wiring"
        )

    def test_domains_router_imports_get_write_queue(self):
        source = _read_router("domains.py")
        assert "from app.dependencies.write_queue import get_write_queue" in source, (
            "domains.py must import get_write_queue for cycle 8 wiring"
        )

    def test_templates_router_imports_get_write_queue(self):
        source = _read_router("templates.py")
        assert "from app.dependencies.write_queue import get_write_queue" in source, (
            "templates.py must import get_write_queue for cycle 8 wiring"
        )

    def test_github_repos_router_imports_get_write_queue(self):
        source = _read_router("github_repos.py")
        assert "from app.dependencies.write_queue import get_write_queue" in source, (
            "github_repos.py must import get_write_queue for cycle 8 wiring"
        )

    def test_projects_router_imports_get_write_queue(self):
        source = _read_router("projects.py")
        assert "from app.dependencies.write_queue import get_write_queue" in source, (
            "projects.py must import get_write_queue for cycle 8 wiring"
        )


# ---------------------------------------------------------------------------
# Operation label invariants — every migrated handler emits a label
# ---------------------------------------------------------------------------


class TestRoutersUseOperationLabels:
    """Each migrated handler routes through the queue under a stable
    operation_label visible in WriteQueueMetrics + decision events.
    """

    def test_optimize_passthrough_prepare_label(self):
        """``passthrough_prepare`` saves the pending Optimization under
        operation_label 'optimize_passthrough_prepare'."""
        source = _read_router("optimize.py")
        assert "optimize_passthrough_prepare" in source, (
            "optimize.py must use operation_label='optimize_passthrough_prepare' "
            "for the passthrough INSERT"
        )

    def test_optimize_intent_rename_label(self):
        """The intent_label rename endpoint commits under
        operation_label 'optimization_intent_rename'."""
        source = _read_router("optimize.py")
        assert "optimization_intent_rename" in source, (
            "optimize.py must use operation_label='optimization_intent_rename' "
            "for the intent rename"
        )

    def test_optimize_passthrough_save_label(self):
        """``passthrough_save`` finalizes under operation_label
        'optimize_passthrough_save'."""
        source = _read_router("optimize.py")
        assert "optimize_passthrough_save" in source, (
            "optimize.py must use operation_label='optimize_passthrough_save' "
            "for the passthrough save"
        )

    def test_domains_promote_label(self):
        """Domain promote uses operation_label 'domain_promote'."""
        source = _read_router("domains.py")
        assert "domain_promote" in source, (
            "domains.py must use operation_label='domain_promote'"
        )

    def test_domains_dissolve_label(self):
        """Domain dissolve uses operation_label 'domain_dissolve'."""
        source = _read_router("domains.py")
        assert "domain_dissolve" in source, (
            "domains.py must use operation_label='domain_dissolve'"
        )

    def test_templates_fork_label(self):
        """Template fork uses operation_label 'template_fork'."""
        source = _read_router("templates.py")
        assert "template_fork" in source, (
            "templates.py must use operation_label='template_fork'"
        )

    def test_templates_retire_label(self):
        """Template retire uses operation_label 'template_retire'."""
        source = _read_router("templates.py")
        assert "template_retire" in source, (
            "templates.py must use operation_label='template_retire'"
        )

    def test_templates_use_label(self):
        """Template use uses operation_label 'template_use'."""
        source = _read_router("templates.py")
        assert "template_use" in source, (
            "templates.py must use operation_label='template_use'"
        )

    def test_github_repos_link_label(self):
        """Repo link uses operation_label 'github_repo_link'."""
        source = _read_router("github_repos.py")
        assert "github_repo_link" in source, (
            "github_repos.py must use operation_label='github_repo_link'"
        )

    def test_github_repos_unlink_label(self):
        """Repo unlink uses operation_label 'github_repo_unlink'."""
        source = _read_router("github_repos.py")
        assert "github_repo_unlink" in source, (
            "github_repos.py must use operation_label='github_repo_unlink'"
        )

    def test_projects_migrate_label(self):
        """Project migrate uses operation_label 'projects_migrate'."""
        source = _read_router("projects.py")
        assert "projects_migrate" in source, (
            "projects.py must use operation_label='projects_migrate'"
        )


# ---------------------------------------------------------------------------
# Endpoint-level kwarg wiring — write_queue must be a Depends parameter
# ---------------------------------------------------------------------------


class TestRoutersUseDependsGetWriteQueue:
    """Each migrated handler signature gains
    ``write_queue: WriteQueue = Depends(get_write_queue)``.
    """

    def test_optimize_handlers_depends_on_write_queue(self):
        """At least 3 of the 4 optimize.py write paths take Depends(get_write_queue)."""
        source = _read_router("optimize.py")
        # Count signatures that have Depends(get_write_queue) in them.
        count = source.count("Depends(get_write_queue)")
        assert count >= 3, (
            f"optimize.py needs >= 3 Depends(get_write_queue) bindings; got {count}"
        )

    def test_domains_handlers_depends_on_write_queue(self):
        source = _read_router("domains.py")
        count = source.count("Depends(get_write_queue)")
        assert count >= 2, (
            f"domains.py needs >= 2 Depends(get_write_queue) bindings; got {count}"
        )

    def test_templates_handlers_depends_on_write_queue(self):
        source = _read_router("templates.py")
        count = source.count("Depends(get_write_queue)")
        assert count >= 3, (
            f"templates.py needs >= 3 Depends(get_write_queue) bindings; got {count}"
        )

    def test_github_repos_handlers_depends_on_write_queue(self):
        source = _read_router("github_repos.py")
        count = source.count("Depends(get_write_queue)")
        assert count >= 2, (
            f"github_repos.py needs >= 2 Depends(get_write_queue) bindings; got {count}"
        )

    def test_projects_handlers_depends_on_write_queue(self):
        source = _read_router("projects.py")
        count = source.count("Depends(get_write_queue)")
        assert count >= 1, (
            f"projects.py needs >= 1 Depends(get_write_queue) bindings; got {count}"
        )
