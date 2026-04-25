"""Auto-update service — version detection, update execution, validation."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from app.config import UPDATE_TAG_PATTERN, settings

logger = logging.getLogger(__name__)

MARKER_FILE = "data/.update_pending"
RAW_VERSION_URL = (
    "https://raw.githubusercontent.com/{repo}/main/version.json"
)

# Auto-stash sentinel — the update flow uses this prefix on stash messages
# so it can identify and pop only stashes it created itself, never a
# user's pre-existing stash.
AUTO_STASH_PREFIX = "synthesis-update"
# Drain budget: max seconds to wait for in-flight optimizations to finish
# before forcing the update through. 60s is comfortable for most pipelines
# (Opus 4.7 max-effort optimize phase is ~60-120s, so a long drain may
# still time out — but a 60s wait covers >80% of in-flight calls).
DRAIN_TIMEOUT_SECONDS = 60.0
DRAIN_POLL_INTERVAL_SECONDS = 0.5


# ---------------------------------------------------------------------
# Drain tracker — coordinates update apply with in-flight optimizations
# ---------------------------------------------------------------------


class UpdateInflightTracker:
    """Process-singleton tracker of in-flight optimizations + update lock.

    The pipeline calls ``begin(trace_id)`` at the start of each
    optimization and ``end(trace_id)`` after persistence completes.
    The update flow checks ``running_count`` and waits for it to reach
    zero (with a timeout) before triggering ``init.sh restart``.

    During an active update window, ``update_in_progress`` is True and
    the optimize router returns 503 to new requests so a half-written
    optimization isn't orphaned by the restart.
    """

    def __init__(self) -> None:
        self._running: set[str] = set()
        self._update_in_progress: bool = False
        self._lock = asyncio.Lock()

    @property
    def running_count(self) -> int:
        # Lock-free read by design: ``set.add/discard`` are atomic
        # operations under CPython's GIL, so a torn read is impossible.
        # Drain loop polls this between awaits — new arrivals during
        # drain are bounded by the 503 gate set inside ``begin_update``.
        return len(self._running)

    @property
    def running_trace_ids(self) -> list[str]:
        # Same lock-free reasoning as ``running_count``. ``sorted()`` of
        # a set snapshot is atomic; no need to acquire ``self._lock``.
        return sorted(self._running)

    @property
    def update_in_progress(self) -> bool:
        return self._update_in_progress

    async def begin(self, trace_id: str) -> None:
        async with self._lock:
            self._running.add(trace_id)

    async def end(self, trace_id: str) -> None:
        async with self._lock:
            self._running.discard(trace_id)

    async def begin_update(self) -> None:
        async with self._lock:
            self._update_in_progress = True

    async def end_update(self) -> None:
        async with self._lock:
            self._update_in_progress = False

    async def drain(
        self,
        timeout: float = DRAIN_TIMEOUT_SECONDS,
        poll: float = DRAIN_POLL_INTERVAL_SECONDS,
    ) -> tuple[bool, int]:
        """Wait for ``running_count`` to reach zero or *timeout* expires.

        Returns ``(drained: bool, remaining: int)``. ``drained=True``
        means we're safe to proceed; ``False`` means *timeout* hit and
        the caller should decide whether to force-restart anyway.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self.running_count == 0:
                return (True, 0)
            await asyncio.sleep(poll)
        return (self.running_count == 0, self.running_count)


_inflight_tracker: UpdateInflightTracker | None = None


def get_inflight_tracker() -> UpdateInflightTracker:
    """Process-singleton accessor."""
    global _inflight_tracker  # noqa: PLW0603
    if _inflight_tracker is None:
        _inflight_tracker = UpdateInflightTracker()
    return _inflight_tracker


def set_inflight_tracker(tracker: UpdateInflightTracker | None) -> None:
    """Override singleton (used by tests + lifespan rebinding)."""
    global _inflight_tracker  # noqa: PLW0603
    _inflight_tracker = tracker


# ---------------------------------------------------------------------
# Pre-flight readiness contract
# ---------------------------------------------------------------------


@dataclass
class DirtyFile:
    """One entry in the dirty-tree report.

    ``source`` classifies how the file came to be modified:
      * ``"user_api"`` — recorded in customization_tracker (PUT
        endpoint edit). Auto-stash will preserve.
      * ``"manual_edit"`` — git-tracked file modified on disk but NOT
        in the customization registry (operator hand-edited a config
        / prompt outside the API). Auto-stash will preserve.
      * ``"untracked"`` — git ``??`` entry (never committed). The
        existing dirty-tree check ignored these; we surface them for
        visibility only.
    """

    path: str
    status: str
    source: str
    in_prompts_tree: bool


@dataclass
class PreflightStatus:
    """Comprehensive pre-update readiness probe."""

    can_apply: bool
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dirty_files: list[DirtyFile] = field(default_factory=list)
    user_customizations: list[str] = field(default_factory=list)
    commits_ahead_of_origin: int = 0
    commits_behind_origin: int = 0
    on_detached_head: bool = False
    in_flight_optimizations: int = 0
    in_flight_trace_ids: list[str] = field(default_factory=list)
    will_auto_stash: bool = False
    target_tag: str | None = None
    target_tag_exists_locally: bool = False


@dataclass
class UpdateStatus:
    """Cached result of a version check."""
    current_version: str
    latest_version: str | None = None
    latest_tag: str | None = None
    update_available: bool = False
    changelog: str | None = None
    changelog_entries: list[dict[str, str]] | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    detection_tier: str = "none"


def validate_tag(tag: str) -> None:
    """Validate a git tag against the allowed pattern. Raises ValueError."""
    if not tag or not UPDATE_TAG_PATTERN.match(tag):
        raise ValueError(f"Invalid tag format: {tag!r}")


def compare_versions(local: str, remote: str) -> int:
    """Compare two version strings. Returns -1 (local older), 0, or 1 (local newer).

    Handles -dev suffix by converting to PEP 440 .devN format.
    Pre-release remote versions are treated as older than stable local versions
    UNLESS the local version is also a pre-release.
    """
    try:
        local_v = Version(local.replace("-dev", ".dev0").replace("-rc", "rc"))
        remote_v = Version(remote.replace("-dev", ".dev0").replace("-rc", "rc"))
    except InvalidVersion:
        return 0

    # If local is stable but remote is pre-release, treat local as newer
    local_is_stable = not (local_v.is_prerelease or local_v.is_devrelease)
    remote_is_prerelease = remote_v.is_prerelease or remote_v.is_devrelease
    if local_is_stable and remote_is_prerelease:
        return 1

    if local_v < remote_v:
        return -1
    if local_v > remote_v:
        return 1
    return 0


def _parse_latest_tag(tag_output: str) -> str | None:
    """Parse the latest stable semver tag from git tag --sort=-v:refname output.

    Skips -dev and pre-release tags — only stable releases trigger auto-update.
    Use ``./scripts/release.sh`` to create stable releases from -dev versions.
    """
    for line in tag_output.strip().splitlines():
        tag = line.strip()
        if not tag:
            continue
        if UPDATE_TAG_PATTERN.match(tag):
            try:
                v = Version(tag.lstrip("v").replace("-rc", "rc"))
                if v.is_prerelease or v.is_devrelease:
                    continue
            except InvalidVersion:
                continue
            return tag
    return None


class UpdateService:
    """Manages version detection and update execution."""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._state: UpdateStatus | None = None
        self._lock = asyncio.Lock()

    @property
    def status(self) -> UpdateStatus | None:
        return self._state

    async def check_for_updates(self) -> UpdateStatus:
        """Run 3-tier version detection. Safe to call from background task."""
        try:
            return await self._do_check()
        except Exception as exc:
            logger.warning("Update check failed: %s", exc)
            current = self._read_current_version()
            self._state = UpdateStatus(current_version=current)
            return self._state

    async def _do_check(self) -> UpdateStatus:
        current = self._read_current_version()
        await self._resume_pending_update(current)

        latest_tag = await self._check_git_tags()
        detection_tier = "git_tags"
        if latest_tag is None:
            latest_tag = await self._check_raw_fetch()
            detection_tier = "raw_fetch" if latest_tag else "none"

        if latest_tag is None:
            self._state = UpdateStatus(current_version=current, detection_tier="none")
            return self._state

        latest_version = latest_tag.lstrip("v")
        update_available = compare_versions(current, latest_version) == -1

        changelog = None
        changelog_entries = None
        if update_available:
            changelog, changelog_entries = await self._fetch_changelog(latest_tag)

        self._state = UpdateStatus(
            current_version=current,
            latest_version=latest_version,
            latest_tag=latest_tag,
            update_available=update_available,
            changelog=changelog,
            changelog_entries=changelog_entries,
            detection_tier=detection_tier,
        )

        if update_available:
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("update_available", {
                    "current_version": current,
                    "latest_version": latest_version,
                    "latest_tag": latest_tag,
                    "changelog": changelog,
                    "changelog_entries": changelog_entries,
                })
            except Exception:
                pass

        return self._state

    def _read_current_version(self) -> str:
        try:
            vf = self._root / "version.json"
            return json.loads(vf.read_text())["version"]
        except Exception:
            from app._version import __version__
            return __version__

    async def _check_git_tags(self) -> str | None:
        """Tier 1: git fetch --tags + parse latest semver tag."""
        try:
            fetch = await asyncio.create_subprocess_exec(
                "git", "fetch", "--tags", "--prune-tags",
                cwd=str(self._root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(fetch.wait(), timeout=30)

            tags = await asyncio.create_subprocess_exec(
                "git", "tag", "--sort=-v:refname",
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(tags.communicate(), timeout=10)
            return _parse_latest_tag(stdout.decode())
        except Exception as exc:
            logger.debug("Git tag check failed: %s", exc)
            return None

    async def _check_raw_fetch(self) -> str | None:
        """Tier 2: fetch version.json from GitHub raw content."""
        try:
            import httpx
            url = RAW_VERSION_URL.format(repo=settings.UPSTREAM_REPO)
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                remote_version = resp.json()["version"]
                clean = remote_version.split("-")[0]
                return f"v{clean}"
        except Exception as exc:
            logger.debug("Raw version fetch failed: %s", exc)
            return None

    async def _fetch_changelog(
        self, tag: str
    ) -> tuple[str | None, list[dict[str, str]] | None]:
        """Tier 3: fetch release notes from GitHub Releases API."""
        try:
            from sqlalchemy import select

            from app.database import async_session_factory
            from app.models import GitHubToken
            from app.services.github_client import GitHubClient
            from app.services.github_service import GitHubService

            async with async_session_factory() as db:
                token_q = await db.execute(select(GitHubToken).limit(1))
                token_row = token_q.scalars().first()
                if not token_row:
                    return None, None

                svc = GitHubService(secret_key=settings.resolve_secret_key())
                token = svc.decrypt_token(token_row.token_encrypted)

                client = GitHubClient()
                release = await client.get_release_by_tag(
                    token, settings.UPSTREAM_REPO, tag,
                )
                if not release:
                    return None, None

                body = release.get("body", "")
                entries = _parse_changelog_entries(body)
                return body, entries
        except Exception as exc:
            logger.debug("Changelog fetch failed: %s", exc)
            return None, None

    async def _resume_pending_update(self, current_version: str) -> None:
        """Phase 2: check for pending update marker and validate."""
        marker = self._root / MARKER_FILE
        if not marker.exists():
            return

        try:
            data = json.loads(marker.read_text())
            expected_tag = data.get("tag", "")
            logger.info("Pending update marker found: %s", expected_tag)

            checks = await self.validate_update(expected_tag)
            success = all(c["passed"] for c in checks)

            try:
                from app.services.event_bus import event_bus
                event_bus.publish("update_complete", {
                    "success": success,
                    "tag": expected_tag,
                    "version": current_version,
                    "checks": checks,
                })
            except Exception:
                pass

            if success:
                logger.info("Update to %s validated successfully", expected_tag)
            else:
                logger.warning("Update validation partial failure: %s", checks)
        except Exception as exc:
            logger.warning("Failed to resume pending update: %s", exc)
        finally:
            try:
                marker.unlink(missing_ok=True)
            except Exception:
                pass

    async def validate_update(self, expected_tag: str) -> list[dict[str, Any]]:
        """Run 3-check post-update validation suite."""
        checks: list[dict[str, Any]] = []

        current = self._read_current_version()
        expected_version = expected_tag.lstrip("v")
        version_ok = current.split("-")[0] == expected_version.split("-")[0]
        checks.append({
            "name": "version",
            "passed": version_ok,
            "detail": f"version.json reports {current}" + (
                "" if version_ok else f" (expected {expected_version})"
            ),
        })

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "describe", "--tags", "--exact-match", "HEAD",
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            actual_tag = stdout.decode().strip()
            tag_ok = actual_tag == expected_tag
            checks.append({
                "name": "tag",
                "passed": tag_ok,
                "detail": f"HEAD at {actual_tag}" + (
                    "" if tag_ok else f" (expected {expected_tag})"
                ),
            })
        except Exception as exc:
            checks.append({"name": "tag", "passed": False, "detail": f"git describe failed: {exc}"})

        try:
            proc = await asyncio.create_subprocess_exec(
                str(self._root / "backend" / ".venv" / "bin" / "python"),
                "-m", "alembic", "current",
                cwd=str(self._root / "backend"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode().strip()
            alembic_ok = "(head)" in output
            checks.append({
                "name": "migrations",
                "passed": alembic_ok,
                "detail": "Alembic at head" if alembic_ok else f"Alembic: {output}",
            })
        except Exception as exc:
            checks.append({"name": "migrations", "passed": False, "detail": f"Alembic check failed: {exc}"})

        return checks

    # -- Pre-flight readiness probe (v0.4.6 hardening) ----------------

    async def preflight(self, tag: str | None = None) -> PreflightStatus:
        """Comprehensive update readiness probe.

        Returns a :class:`PreflightStatus` describing every condition
        that affects whether an update can run safely. Frontend calls
        this BEFORE enabling the "Update & Restart" button — the user
        sees dirty files, in-flight optimizations, branch divergence,
        and customization counts before committing.

        ``tag``: optional target tag to validate against the local
        registry. When ``None``, the cached ``latest_tag`` is used (or
        the probe runs without a tag context).
        """
        from app.services.customization_tracker import get_tracker

        target_tag = tag or (self._state.latest_tag if self._state else None)
        status = PreflightStatus(can_apply=True, target_tag=target_tag)

        # 1. Dirty tree analysis with source classification.
        dirty_lines = await self._git_status_porcelain()
        customizations = get_tracker().list_modifications()
        for line in dirty_lines:
            # Porcelain v1: 'XY path' (X=index, Y=worktree, two chars + space)
            if len(line) < 4:
                continue
            xy = line[:2]
            path = line[3:].strip()
            # Some entries quote paths with spaces ("...");
            # strip surrounding quotes if present.
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]

            status_code = xy.strip()
            in_prompts = path.startswith("prompts/")
            untracked = xy == "??"
            if untracked:
                source = "untracked"
            elif path in customizations:
                source = "user_api"
            else:
                source = "manual_edit"
            status.dirty_files.append(DirtyFile(
                path=path, status=status_code, source=source,
                in_prompts_tree=in_prompts,
            ))

        # Files that the auto-stash will preserve — anything tracked +
        # in prompts/ is safe to stash and restore.
        stashable = [
            d for d in status.dirty_files
            if d.in_prompts_tree and d.source != "untracked"
        ]
        status.will_auto_stash = bool(stashable)
        status.user_customizations = sorted(customizations.keys())

        # Manual edits to non-prompt tracked files block — those
        # aren't safe to silently stash because we don't know the
        # operator's intent (could be backend code, CI config, etc.).
        non_prompt_modified = [
            d for d in status.dirty_files
            if not d.in_prompts_tree and d.source != "untracked"
        ]
        if non_prompt_modified:
            paths = ", ".join(sorted(d.path for d in non_prompt_modified[:5]))
            extra = "" if len(non_prompt_modified) <= 5 else f" (+{len(non_prompt_modified) - 5} more)"
            status.blocking_issues.append(
                f"Uncommitted changes outside prompts/: {paths}{extra}. "
                "Commit or stash these before updating.",
            )

        # 2. Branch divergence — local commits ahead of origin/main
        # would be silently orphaned by a pure-tag checkout.
        ahead, behind = await self._git_ahead_behind_origin()
        status.commits_ahead_of_origin = ahead
        status.commits_behind_origin = behind
        if ahead > 0:
            status.warnings.append(
                f"Local branch is {ahead} commit(s) ahead of origin/main. "
                "Update will checkout a tag — your local commits stay reachable "
                "via reflog but become unreferenced from main.",
            )

        # 3. Detached HEAD detection.
        on_detached = await self._git_detached_head()
        status.on_detached_head = on_detached
        if on_detached:
            status.warnings.append(
                "Repository is in detached HEAD state. Update will succeed "
                "but you'll remain detached on the new tag — run "
                "`git checkout main` if you want to track main again.",
            )

        # 4. In-flight optimizations.
        tracker = get_inflight_tracker()
        status.in_flight_optimizations = tracker.running_count
        status.in_flight_trace_ids = tracker.running_trace_ids
        if tracker.running_count > 0:
            status.warnings.append(
                f"{tracker.running_count} optimization(s) in flight. "
                f"Apply will wait up to {int(DRAIN_TIMEOUT_SECONDS)}s for them "
                "to finish before triggering the restart.",
            )

        # 5. Tag validation when a target was supplied.
        if target_tag:
            try:
                validate_tag(target_tag)
            except ValueError as exc:
                status.blocking_issues.append(str(exc))

            tag_exists = await self._git_tag_exists(target_tag)
            status.target_tag_exists_locally = tag_exists
            if not tag_exists:
                status.warnings.append(
                    f"Tag {target_tag} not yet fetched locally — "
                    "apply will fetch it before checkout.",
                )

        # 6. Concurrent update lock.
        # Use the inflight tracker flag (set INSIDE apply_update's own
        # lock block) instead of ``self._lock.locked()`` — the latter
        # is True when apply_update calls preflight as part of its own
        # flow, which would create a false positive. The tracker flag
        # is only set during the actual apply, never during preflight.
        if get_inflight_tracker().update_in_progress:
            status.blocking_issues.append("Another update is already in progress.")

        if status.blocking_issues:
            status.can_apply = False
        return status

    async def _git_status_porcelain(self) -> list[str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return [
            line for line in stdout.decode().splitlines()
            if line.strip()
        ]

    async def _git_ahead_behind_origin(self) -> tuple[int, int]:
        """Return (commits_ahead, commits_behind) of HEAD vs origin/main."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-list", "--left-right", "--count",
                "HEAD...origin/main",
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return (0, 0)
            parts = stdout.decode().strip().split()
            if len(parts) != 2:
                return (0, 0)
            return (int(parts[0]), int(parts[1]))
        except (ValueError, asyncio.TimeoutError, OSError):
            return (0, 0)

    async def _git_detached_head(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "symbolic-ref", "-q", "HEAD",
                cwd=str(self._root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            # Returns 0 when HEAD is a symbolic ref (i.e. on a branch);
            # nonzero ⇒ detached HEAD.
            return proc.returncode != 0
        except (asyncio.TimeoutError, OSError):
            return False

    async def _git_tag_exists(self, tag: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "tag", "-l", tag,
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return tag in stdout.decode().strip().splitlines()
        except (asyncio.TimeoutError, OSError):
            return False

    # -- Step-by-step apply (v0.4.6 hardening) ------------------------

    async def _publish_step(
        self,
        step: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        """Publish ``update_step`` SSE event.

        Frontend renders these as a per-step progress timeline so the
        user knows whether the apply is stuck on git fetch, alembic
        migration, or service restart.
        """
        try:
            from app.services.event_bus import event_bus

            payload: dict[str, Any] = {"step": step, "status": status}
            if detail:
                payload["detail"] = detail
            event_bus.publish("update_step", payload)
        except Exception:
            logger.debug("update_step event publish failed", exc_info=True)

    async def apply_update(
        self,
        tag: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Phase 1: pre-flight, drain, stash, checkout, deps, alembic, restart.

        Args:
            tag: target git tag (validated against ``UPDATE_TAG_PATTERN``).
            force: when True, bypass blocking issues from ``preflight()``
                except the basic tag/lock validations. The pre-flight
                non-prompt-modified-files block is still enforced —
                only branch-ahead and in-flight warnings are bypassed.
        """
        if self._lock.locked():
            raise RuntimeError("Update already in progress")

        async with self._lock:
            await self._publish_step("preflight", "running")
            validate_tag(tag)

            preflight = await self.preflight(tag=tag)
            if preflight.blocking_issues and not force:
                # Blocking issues always halt — even force=True doesn't
                # bypass safety here (only warnings are bypassable).
                raise ValueError(
                    "Pre-flight blocked: " + " | ".join(preflight.blocking_issues),
                )
            await self._publish_step(
                "preflight", "done",
                detail=f"warnings={len(preflight.warnings)} dirty={len(preflight.dirty_files)}",
            )

            tracker = get_inflight_tracker()
            await tracker.begin_update()
            try:
                # 1. Drain in-flight optimizations.
                if tracker.running_count > 0:
                    await self._publish_step(
                        "drain", "running",
                        detail=f"{tracker.running_count} in-flight",
                    )
                    drained, remaining = await tracker.drain()
                    if not drained and not force:
                        raise RuntimeError(
                            f"{remaining} optimization(s) still running after drain. "
                            f"Pass force=True to proceed anyway.",
                        )
                    await self._publish_step("drain", "done")

                # 2. Confirm tag is local (covers the case where
                # preflight was called before fetch).
                if not await self._git_tag_exists(tag):
                    await self._publish_step("fetch_tags", "running")
                    fetch = await asyncio.create_subprocess_exec(
                        "git", "fetch", "--tags", "--prune-tags",
                        cwd=str(self._root),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(fetch.wait(), timeout=60)
                    await self._publish_step("fetch_tags", "done")
                    if not await self._git_tag_exists(tag):
                        raise ValueError(f"Tag {tag} does not exist locally")

                # 3. Snapshot current HEAD (for rollback).
                old_head = await self._git_head_sha()

                # 4. Auto-stash dirty prompts/ files.
                stash_created = False
                if preflight.will_auto_stash:
                    await self._publish_step("stash", "running")
                    stash_created = await self._auto_stash(tag)
                    await self._publish_step(
                        "stash", "done",
                        detail="created" if stash_created else "nothing-to-stash",
                    )

                # 5. Checkout the target tag.
                await self._publish_step("checkout", "running")
                checkout = await asyncio.create_subprocess_exec(
                    "git", "checkout", f"refs/tags/{tag}",
                    cwd=str(self._root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(checkout.communicate(), timeout=30)
                if checkout.returncode != 0:
                    await self._publish_step(
                        "checkout", "failed",
                        detail=stderr.decode()[:200],
                    )
                    if stash_created:
                        # Restore the user's edits before re-raising.
                        await self._auto_stash_pop()
                    raise RuntimeError(f"git checkout failed: {stderr.decode()}")
                await self._publish_step("checkout", "done")

                # 6. Conditional deps install.
                try:
                    await self._publish_step("deps", "running")
                    await self._install_deps_if_changed(old_head)
                    await self._publish_step("deps", "done")
                except Exception as dep_exc:
                    logger.warning("Dependency install issue: %s", dep_exc)
                    await self._publish_step(
                        "deps", "warning",
                        detail=str(dep_exc)[:200],
                    )

                # 7. Alembic migrations.
                try:
                    await self._publish_step("migrate", "running")
                    await self._run_alembic_upgrade()
                    await self._publish_step("migrate", "done")
                except Exception as alembic_exc:
                    logger.error("Alembic upgrade failed, rolling back: %s", alembic_exc)
                    await self._publish_step(
                        "migrate", "failed",
                        detail=str(alembic_exc)[:200],
                    )
                    rollback = await asyncio.create_subprocess_exec(
                        "git", "checkout", old_head,
                        cwd=str(self._root),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(rollback.wait(), timeout=30)
                    if stash_created:
                        await self._auto_stash_pop()
                    raise RuntimeError(
                        f"Migration failed: {alembic_exc}. Code rolled back to previous version.",
                    ) from alembic_exc

                # 8. Pop the auto-stash so user customizations survive.
                stash_pop_conflicts: list[str] = []
                if stash_created:
                    await self._publish_step("pop_stash", "running")
                    stash_pop_conflicts = await self._auto_stash_pop()
                    await self._publish_step(
                        "pop_stash",
                        "warning" if stash_pop_conflicts else "done",
                        detail=(
                            f"conflicts in {len(stash_pop_conflicts)} file(s)"
                            if stash_pop_conflicts else "clean"
                        ),
                    )

                # 9. Marker for Phase 2 validation post-restart.
                marker = self._root / MARKER_FILE
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(json.dumps({
                    "tag": tag,
                    "old_head": old_head,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "stash_pop_conflicts": stash_pop_conflicts,
                }))

                # 10. Detached restart.
                await self._publish_step("restart", "running")

                async def _deferred_restart() -> None:
                    await asyncio.sleep(1)
                    import subprocess as _sp
                    _sp.Popen(
                        [str(self._root / "init.sh"), "restart"],
                        start_new_session=True,
                        stdout=_sp.DEVNULL,
                        stderr=_sp.DEVNULL,
                        close_fds=True,
                        cwd=str(self._root),
                    )

                asyncio.create_task(_deferred_restart())
                return {
                    "status": "restarting",
                    "tag": tag,
                    "stash_pop_conflicts": stash_pop_conflicts,
                }
            finally:
                await tracker.end_update()

    async def _git_head_sha(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return stdout.decode().strip()

    async def _auto_stash(self, tag: str) -> bool:
        """Stash dirty ``prompts/`` files (PUT-edited strategies + manual).

        Returns True if a stash was actually created. The stash message
        is prefixed with :data:`AUTO_STASH_PREFIX` so ``_auto_stash_pop``
        can identify it without colliding with operator stashes.
        """
        message = f"{AUTO_STASH_PREFIX}-{tag}-{datetime.now(timezone.utc).isoformat()}"
        proc = await asyncio.create_subprocess_exec(
            "git", "stash", "push", "-m", message, "--", "prompts/",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = (stdout.decode() + stderr.decode()).strip()
        # ``git stash push`` says "No local changes to save" when there
        # was nothing to stash — we treat that as no-op success.
        if "No local changes" in out or "nothing to save" in out.lower():
            return False
        if proc.returncode != 0:
            logger.warning("Auto-stash failed: %s", out)
            return False
        logger.info("Auto-stashed prompt edits with message %r", message)
        return True

    async def _auto_stash_pop(self) -> list[str]:
        """Pop the most recent stash IF it was created by us.

        Returns a list of conflict file paths. Empty list = clean pop.
        Non-empty = the stash content overlaps with the new tag's
        version of the file; user must manually resolve via
        ``git status`` (file shows as 'unmerged').
        """
        # Find the most recent auto-stash. ``git stash list`` outputs
        # ``stash@{N}: WIP on detached: <message>``.
        proc = await asyncio.create_subprocess_exec(
            "git", "stash", "list",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        # SEV-MEDIUM hardening (review #1): require ``synthesis-update-``
        # WITH the trailing hyphen so an operator stash whose message
        # coincidentally contains ``synthesis-update`` (e.g.
        # ``"WIP synthesis-update-debugging"``) is NOT mistakenly popped.
        # All real auto-stashes follow the format
        # ``synthesis-update-<tag>-<iso8601>``.
        sentinel = f"{AUTO_STASH_PREFIX}-"
        target_ref: str | None = None
        for line in stdout.decode().splitlines():
            if sentinel in line:
                target_ref = line.split(":")[0].strip()
                break
        if target_ref is None:
            return []

        proc = await asyncio.create_subprocess_exec(
            "git", "stash", "pop", target_ref,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        combined = (stdout.decode() + stderr.decode())

        if proc.returncode == 0 and "CONFLICT" not in combined:
            return []

        # Conflict — collect the unmerged paths so we can surface them.
        conflicts: list[str] = []
        unmerged_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", "--diff-filter=U",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        unmerged_stdout, _ = await asyncio.wait_for(unmerged_proc.communicate(), timeout=10)
        for path in unmerged_stdout.decode().splitlines():
            path = path.strip()
            if path:
                conflicts.append(path)
        if conflicts:
            logger.warning(
                "Auto-stash pop produced %d conflict(s): %s",
                len(conflicts), ", ".join(conflicts[:5]),
            )
        return conflicts

    async def _install_deps_if_changed(self, old_head: str) -> None:
        """Install backend/frontend deps if their lock files changed."""
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", old_head, "--",
            "backend/requirements.txt",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if stdout.decode().strip():
            logger.info("requirements.txt changed — installing backend deps")
            pip = await asyncio.create_subprocess_exec(
                str(self._root / "backend" / ".venv" / "bin" / "pip"),
                "install", "-r", "requirements.txt",
                cwd=str(self._root / "backend"),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(pip.wait(), timeout=120)

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", old_head, "--",
            "frontend/package-lock.json",
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if stdout.decode().strip():
            logger.info("package-lock.json changed — installing frontend deps")
            npm = await asyncio.create_subprocess_exec(
                "npm", "ci",
                cwd=str(self._root / "frontend"),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(npm.wait(), timeout=120)

    async def _run_alembic_upgrade(self) -> None:
        """Run alembic upgrade head in the backend venv."""
        proc = await asyncio.create_subprocess_exec(
            str(self._root / "backend" / ".venv" / "bin" / "python"),
            "-m", "alembic", "upgrade", "head",
            cwd=str(self._root / "backend"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode().strip() or stdout.decode().strip())


def _parse_changelog_entries(body: str) -> list[dict[str, str]]:
    """Parse GitHub release body into categorized entries."""
    entries: list[dict[str, str]] = []
    category = "Changed"
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("##"):
            cat = line.lstrip("#").strip()
            if cat in ("Added", "Changed", "Fixed", "Removed", "Deprecated"):
                category = cat
            continue
        if line.startswith("- ") or line.startswith("* "):
            text = line.lstrip("-* ").strip()
            if text:
                entries.append({"category": category, "text": text})
    return entries
