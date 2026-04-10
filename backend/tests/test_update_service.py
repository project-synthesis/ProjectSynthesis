"""Tests for UpdateService — version detection and tag validation."""

from unittest.mock import AsyncMock

import pytest

from app.services.update_service import (
    MARKER_FILE,
    UpdateService,
    UpdateStatus,
    _parse_changelog_entries,
    _parse_latest_tag,
    compare_versions,
    validate_tag,
)

# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

class TestCompareVersions:
    def test_dev_less_than_release(self):
        assert compare_versions("0.3.20-dev", "0.4.0") == -1

    def test_equal_versions(self):
        assert compare_versions("0.4.0", "0.4.0") == 0

    def test_newer_local(self):
        assert compare_versions("0.5.0", "0.4.0") == 1

    def test_patch_bump(self):
        assert compare_versions("0.3.19", "0.3.20") == -1

    def test_prerelease_excluded(self):
        assert compare_versions("0.4.0", "0.5.0-dev") == 1


class TestValidateTag:
    def test_valid_semver_tag(self):
        validate_tag("v0.4.0")

    def test_valid_prerelease_tag(self):
        validate_tag("v1.0.0-rc.1")

    def test_rejects_shell_injection(self):
        with pytest.raises(ValueError, match="Invalid tag format"):
            validate_tag("v1.0; rm -rf /")

    def test_rejects_argument_injection(self):
        with pytest.raises(ValueError, match="Invalid tag format"):
            validate_tag("--help")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid tag format"):
            validate_tag("")

    def test_rejects_no_v_prefix(self):
        with pytest.raises(ValueError, match="Invalid tag format"):
            validate_tag("0.4.0")


class TestUpdateStatus:
    def test_no_update(self):
        status = UpdateStatus(
            current_version="0.4.0", latest_version="0.4.0",
            latest_tag="v0.4.0", update_available=False,
        )
        assert not status.update_available

    def test_update_available(self):
        status = UpdateStatus(
            current_version="0.3.20-dev", latest_version="0.4.0",
            latest_tag="v0.4.0", update_available=True,
            changelog="## Added\n- New feature",
        )
        assert status.update_available
        assert status.latest_tag == "v0.4.0"


# ---------------------------------------------------------------------------
# Pre-release filtering
# ---------------------------------------------------------------------------

class TestCompareVersionsPrerelease:
    def test_stable_local_ignores_dev_remote(self):
        assert compare_versions("0.4.0", "0.5.0-dev") == 1

    def test_stable_local_ignores_rc_remote(self):
        assert compare_versions("0.4.0", "0.5.0-rc.1") == 1

    def test_prerelease_local_sees_prerelease_remote(self):
        assert compare_versions("0.5.0-rc.1", "0.5.0-rc.2") == -1

    def test_dev_local_sees_stable_remote(self):
        assert compare_versions("0.4.0-dev", "0.4.0") == -1


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

class TestParseLatestTag:
    def test_finds_latest_stable(self):
        assert _parse_latest_tag("v0.5.0\nv0.4.0\nv0.3.0\n") == "v0.5.0"

    def test_skips_prerelease(self):
        assert _parse_latest_tag("v0.5.0-rc.1\nv0.4.0\n") == "v0.4.0"

    def test_empty_output(self):
        assert _parse_latest_tag("") is None

    def test_no_matching_tags(self):
        assert _parse_latest_tag("not-a-tag\nfoo\n") is None

    def test_dev_tags_skipped(self):
        assert _parse_latest_tag("v0.5.0-dev\nv0.4.0-dev\nv0.3.0\n") == "v0.3.0"

    def test_only_prerelease_tags(self):
        assert _parse_latest_tag("v0.5.0-rc.1\nv0.4.0-dev\n") is None

    def test_whitespace_lines(self):
        assert _parse_latest_tag("  \n\nv0.4.0\n  \n") == "v0.4.0"

    def test_mixed_tags_and_noise(self):
        assert _parse_latest_tag("latest\nrelease-2026\nv0.4.0\nv0.3.0\n") == "v0.4.0"


# ---------------------------------------------------------------------------
# Changelog parsing
# ---------------------------------------------------------------------------

class TestParseChangelog:
    def test_categorized_entries(self):
        body = "## Added\n- New feature\n- Another feature\n## Fixed\n- Bug fix\n"
        entries = _parse_changelog_entries(body)
        assert len(entries) == 3
        assert entries[0] == {"category": "Added", "text": "New feature"}
        assert entries[2] == {"category": "Fixed", "text": "Bug fix"}

    def test_empty_body(self):
        assert _parse_changelog_entries("") == []

    def test_no_category_header(self):
        entries = _parse_changelog_entries("- Some improvement\n- Another one\n")
        assert len(entries) == 2
        assert all(e["category"] == "Changed" for e in entries)

    def test_asterisk_bullets(self):
        entries = _parse_changelog_entries("## Fixed\n* Bug one\n* Bug two\n")
        assert len(entries) == 2
        assert entries[0] == {"category": "Fixed", "text": "Bug one"}

    def test_blank_lines_between_entries(self):
        entries = _parse_changelog_entries("## Added\n\n- Feature A\n\n- Feature B\n")
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# Unhappy paths
# ---------------------------------------------------------------------------

class TestValidateTagUnhappy:
    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError):
            validate_tag("v1.0.0/../../etc/passwd")

    def test_rejects_newline_injection(self):
        with pytest.raises(ValueError):
            validate_tag("v1.0.0\n--exec")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError):
            validate_tag("v1.0.0 --help")

    def test_rejects_unicode(self):
        with pytest.raises(ValueError):
            validate_tag("v1.0.0\u0000")

    def test_rejects_only_v(self):
        with pytest.raises(ValueError):
            validate_tag("v")


class TestCompareVersionsEdgeCases:
    def test_rc_vs_release(self):
        assert compare_versions("0.4.0-rc.1", "0.4.0") == -1

    def test_different_dev_versions(self):
        assert compare_versions("0.3.20-dev", "0.3.21-dev") == -1

    def test_invalid_version_string(self):
        assert compare_versions("not-a-version", "0.4.0") == 0

    def test_both_invalid(self):
        assert compare_versions("abc", "xyz") == 0

    def test_major_bump(self):
        assert compare_versions("0.99.99", "1.0.0") == -1


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_service_status_default(tmp_path):
    svc = UpdateService(project_root=tmp_path)
    assert svc.status is None


@pytest.mark.asyncio
async def test_check_for_updates_no_git(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    svc = UpdateService(project_root=tmp_path)
    status = await svc.check_for_updates()
    assert status.current_version == "0.3.20-dev"
    assert status.detection_tier in ("none", "raw_fetch")


@pytest.mark.asyncio
async def test_check_for_updates_reads_version_json(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "1.2.3"}')
    svc = UpdateService(project_root=tmp_path)
    status = await svc.check_for_updates()
    assert status.current_version == "1.2.3"


@pytest.mark.asyncio
async def test_apply_update_rejects_invalid_tag(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    svc = UpdateService(project_root=tmp_path)
    with pytest.raises(ValueError, match="Invalid tag format"):
        await svc.apply_update("not-a-tag")


@pytest.mark.asyncio
async def test_apply_update_rejects_nonexistent_tag(tmp_path):
    import subprocess as _sp
    _sp.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')

    svc = UpdateService(project_root=tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        await svc.apply_update("v99.99.99")


@pytest.mark.asyncio
async def test_validate_update_reports_failures(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    svc = UpdateService(project_root=tmp_path)
    checks = await svc.validate_update("v0.4.0")
    assert not checks[0]["passed"]  # version mismatch
    assert not checks[1]["passed"]  # no git repo
    assert not checks[2]["passed"]  # no alembic


@pytest.mark.asyncio
async def test_marker_file_lifecycle(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    marker = tmp_path / MARKER_FILE
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"tag": "v0.4.0", "old_head": "abc123"}')
    assert marker.exists()

    svc = UpdateService(project_root=tmp_path)
    await svc._resume_pending_update("0.4.0")
    assert not marker.exists()


@pytest.mark.asyncio
async def test_tier2_raw_fetch_fallback(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    svc = UpdateService(project_root=tmp_path)
    svc._check_git_tags = AsyncMock(return_value=None)
    svc._check_raw_fetch = AsyncMock(return_value="v0.4.0")

    status = await svc._do_check()
    assert status.detection_tier == "raw_fetch"
    assert status.latest_tag == "v0.4.0"
    assert status.update_available is True


@pytest.mark.asyncio
async def test_tier3_changelog_enrichment(tmp_path):
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    svc = UpdateService(project_root=tmp_path)
    svc._check_git_tags = AsyncMock(return_value="v0.4.0")
    svc._fetch_changelog = AsyncMock(return_value=(
        "## Added\n- New feature\n## Fixed\n- Bug fix",
        [{"category": "Added", "text": "New feature"}, {"category": "Fixed", "text": "Bug fix"}],
    ))

    status = await svc._do_check()
    assert status.changelog is not None
    assert len(status.changelog_entries) == 2


@pytest.mark.asyncio
async def test_apply_update_rejects_dirty_tree(tmp_path):
    """apply_update raises ValueError when tracked files have uncommitted changes."""
    import subprocess as _sp
    _sp.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    _sp.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "tag", "v0.4.0"], cwd=str(tmp_path), capture_output=True)
    # Create uncommitted tracked change
    (tmp_path / "version.json").write_text('{"version": "dirty"}')

    svc = UpdateService(project_root=tmp_path)
    with pytest.raises(ValueError, match="Uncommitted changes"):
        await svc.apply_update("v0.4.0")


@pytest.mark.asyncio
async def test_concurrent_update_rejected(tmp_path):
    import subprocess as _sp
    _sp.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "tag", "v0.4.0"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')

    svc = UpdateService(project_root=tmp_path)
    await svc._lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already in progress"):
            await svc.apply_update("v0.4.0")
    finally:
        svc._lock.release()


@pytest.mark.asyncio
async def test_apply_update_rollback_on_alembic_failure(tmp_path):
    import subprocess as _sp
    _sp.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "version.json").write_text('{"version": "0.3.20-dev"}')
    _sp.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "tag", "v0.4.0"], cwd=str(tmp_path), capture_output=True)
    # Add a second commit so old_head != v0.4.0 SHA; rollback must return here
    (tmp_path / "version.json").write_text('{"version": "0.4.1-dev"}')
    _sp.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    _sp.run(["git", "commit", "-m", "bump to dev"], cwd=str(tmp_path), capture_output=True)

    svc = UpdateService(project_root=tmp_path)
    svc._run_alembic_upgrade = AsyncMock(side_effect=RuntimeError("migration failed"))
    svc._install_deps_if_changed = AsyncMock()

    with pytest.raises(RuntimeError, match="Migration failed"):
        await svc.apply_update("v0.4.0")

    result = _sp.run(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert "v0.4.0" not in result.stdout
