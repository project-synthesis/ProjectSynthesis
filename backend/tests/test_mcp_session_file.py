"""Unit tests for MCPSessionFile helper.

Tests cover: read, write, update, delete, is_capability_fresh,
is_activity_stale, and should_skip_downgrade.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.mcp_session_file import MCPSessionFile


@pytest.fixture()
def session_file(tmp_path):
    """Return an MCPSessionFile backed by a temp directory."""
    return MCPSessionFile(tmp_path)


# ------------------------------------------------------------------
# read / write
# ------------------------------------------------------------------


class TestReadWrite:
    def test_read_returns_none_when_missing(self, session_file):
        assert session_file.read() is None

    def test_write_then_read(self, session_file):
        data = {"sampling_capable": True, "written_at": "2025-01-01T00:00:00+00:00"}
        session_file.write(data)
        assert session_file.read() == data

    def test_read_returns_none_on_corrupt_json(self, session_file):
        session_file._path.write_text("not json", encoding="utf-8")
        assert session_file.read() is None


# ------------------------------------------------------------------
# update
# ------------------------------------------------------------------


class TestUpdate:
    def test_update_returns_none_when_missing(self, session_file):
        assert session_file.update(foo="bar") is None

    def test_update_merges_fields(self, session_file):
        session_file.write({"a": 1, "b": 2})
        result = session_file.update(b=99, c=3)
        assert result == {"a": 1, "b": 99, "c": 3}
        # Verify persisted
        assert session_file.read() == {"a": 1, "b": 99, "c": 3}


# ------------------------------------------------------------------
# delete
# ------------------------------------------------------------------


class TestDelete:
    def test_delete_returns_false_when_missing(self, session_file):
        assert session_file.delete() is False

    def test_delete_removes_file(self, session_file):
        session_file.write({"test": True})
        assert session_file.delete() is True
        assert session_file.read() is None


# ------------------------------------------------------------------
# is_capability_fresh
# ------------------------------------------------------------------


class TestIsCapabilityFresh:
    def test_fresh_within_window(self):
        data = {"written_at": datetime.now(timezone.utc).isoformat()}
        assert MCPSessionFile.is_capability_fresh(data) is True

    def test_stale_outside_window(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=60)
        data = {"written_at": old.isoformat()}
        assert MCPSessionFile.is_capability_fresh(data) is False

    def test_missing_written_at(self):
        assert MCPSessionFile.is_capability_fresh({}) is False


# ------------------------------------------------------------------
# is_activity_stale
# ------------------------------------------------------------------


class TestIsActivityStale:
    def test_recent_activity_not_stale(self):
        data = {"last_activity": datetime.now(timezone.utc).isoformat()}
        assert MCPSessionFile.is_activity_stale(data) is False

    def test_old_activity_is_stale(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        data = {"last_activity": old.isoformat()}
        assert MCPSessionFile.is_activity_stale(data) is True

    def test_missing_last_activity(self):
        assert MCPSessionFile.is_activity_stale({}) is False


# ------------------------------------------------------------------
# should_skip_downgrade
# ------------------------------------------------------------------


class TestShouldSkipDowngrade:
    def test_skip_when_fresh_true(self, session_file):
        session_file.write({
            "sampling_capable": True,
            "written_at": datetime.now(timezone.utc).isoformat(),
        })
        assert session_file.should_skip_downgrade() is True

    def test_no_skip_when_false(self, session_file):
        session_file.write({
            "sampling_capable": False,
            "written_at": datetime.now(timezone.utc).isoformat(),
        })
        assert session_file.should_skip_downgrade() is False

    def test_no_skip_when_stale(self, session_file):
        old = datetime.now(timezone.utc) - timedelta(minutes=60)
        session_file.write({
            "sampling_capable": True,
            "written_at": old.isoformat(),
        })
        assert session_file.should_skip_downgrade() is False

    def test_no_skip_when_missing(self, session_file):
        assert session_file.should_skip_downgrade() is False


# ------------------------------------------------------------------
# write_session
# ------------------------------------------------------------------


class TestWriteSession:
    def test_writes_required_fields(self, session_file):
        session_file.write_session(True)
        data = session_file.read()
        assert data is not None
        assert data["sampling_capable"] is True
        assert "written_at" in data
        assert "last_activity" in data
        assert data["written_at"] == data["last_activity"]

    def test_includes_sse_streams_when_provided(self, session_file):
        session_file.write_session(False, sse_streams=3)
        data = session_file.read()
        assert data is not None
        assert data["sse_streams"] == 3

    def test_omits_sse_streams_when_none(self, session_file):
        session_file.write_session(True)
        data = session_file.read()
        assert data is not None
        assert "sse_streams" not in data


# ------------------------------------------------------------------
# detect_disconnect
# ------------------------------------------------------------------


class TestDetectDisconnect:
    def test_active_streams_means_connected(self, session_file):
        data = {"sse_streams": 2, "last_activity": "2000-01-01T00:00:00+00:00"}
        assert session_file.detect_disconnect(data) is False

    def test_zero_streams_means_disconnected(self, session_file):
        data = {"sse_streams": 0}
        assert session_file.detect_disconnect(data) is True

    def test_missing_streams_fresh_activity_means_connected(self, session_file):
        data = {"last_activity": datetime.now(timezone.utc).isoformat()}
        assert session_file.detect_disconnect(data) is False

    def test_missing_streams_stale_activity_means_disconnected(self, session_file):
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        data = {"last_activity": old.isoformat()}
        assert session_file.detect_disconnect(data) is True

    def test_missing_streams_missing_activity_means_not_disconnected(self, session_file):
        """No sse_streams and no last_activity → is_activity_stale returns False."""
        assert session_file.detect_disconnect({}) is False
