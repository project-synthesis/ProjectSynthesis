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
    """Parse the latest stable semver tag from git tag --sort=-v:refname output."""
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

    async def apply_update(self, tag: str) -> dict[str, Any]:
        """Phase 1: validate, checkout, deps, alembic, marker, detached restart."""
        if self._lock.locked():
            raise RuntimeError("Update already in progress")

        async with self._lock:
            validate_tag(tag)

            proc = await asyncio.create_subprocess_exec(
                "git", "tag", "-l", tag,
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if tag not in stdout.decode().strip().splitlines():
                raise ValueError(f"Tag {tag} does not exist locally")

            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            dirty = [
                line for line in stdout.decode().strip().splitlines()
                if line.strip() and not line.strip().startswith("??")
            ]
            if dirty:
                raise ValueError("Uncommitted changes detected. Commit or stash before updating.")

            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            old_head = stdout.decode().strip()

            fetch = await asyncio.create_subprocess_exec(
                "git", "fetch", "--tags", "--prune-tags",
                cwd=str(self._root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(fetch.wait(), timeout=60)

            checkout = await asyncio.create_subprocess_exec(
                "git", "checkout", f"refs/tags/{tag}",
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(checkout.communicate(), timeout=30)
            if checkout.returncode != 0:
                raise RuntimeError(f"git checkout failed: {stderr.decode()}")

            try:
                await self._install_deps_if_changed(old_head)
            except Exception as dep_exc:
                logger.warning("Dependency install issue: %s", dep_exc)

            try:
                await self._run_alembic_upgrade()
            except Exception as alembic_exc:
                logger.error("Alembic upgrade failed, rolling back: %s", alembic_exc)
                rollback = await asyncio.create_subprocess_exec(
                    "git", "checkout", old_head,
                    cwd=str(self._root),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(rollback.wait(), timeout=30)
                raise RuntimeError(
                    f"Migration failed: {alembic_exc}. Code rolled back to previous version."
                ) from alembic_exc

            marker = self._root / MARKER_FILE
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({
                "tag": tag,
                "old_head": old_head,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

            import subprocess as _sp
            _sp.Popen(
                [str(self._root / "init.sh"), "restart"],
                start_new_session=True,
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                close_fds=True,
                cwd=str(self._root),
            )

            return {"status": "restarting", "tag": tag}

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
