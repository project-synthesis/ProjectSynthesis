"""Dedicated unit coverage for ``services.error_logger``.

``ErrorLogger`` sits at 59% isolated coverage — the init-path is hit
transitively via the backend lifespan, but ``read_errors``, ``rotate``,
and the ``log_error`` failure paths are not. This file pins:

1. ``get_error_logger`` / ``set_error_logger`` singleton contract.
2. ``log_error`` — writes a well-formed JSON line; optional fields
   round-trip; OSError on write degrades to a warning (never raises).
3. ``read_errors`` — default date, explicit date, missing file, offset
   + limit slicing, skips empty lines, recovers from malformed JSON.
4. ``rotate`` — retention cutoff; skips files with malformed names;
   returns the deleted count.

Copyright 2025-2026 Project Synthesis contributors.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

import app.services.error_logger as el_module
from app.services.error_logger import (
    ErrorLogger,
    get_error_logger,
    set_error_logger,
)

# ---------------------------------------------------------------------------
# Singleton plumbing
# ---------------------------------------------------------------------------

class TestSingleton:
    def setup_method(self):
        # Reset the module-level singleton before each test so the tests
        # remain order-independent.
        el_module._instance = None

    def teardown_method(self):
        el_module._instance = None

    def test_get_before_set_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_error_logger()

    def test_set_then_get_returns_same_instance(self, tmp_path):
        inst = ErrorLogger(errors_dir=tmp_path)
        set_error_logger(inst)
        assert get_error_logger() is inst


# ---------------------------------------------------------------------------
# log_error
# ---------------------------------------------------------------------------

class TestLogError:
    def test_writes_one_jsonl_line_with_required_fields(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        el.log_error(
            service="backend",
            level="error",
            module="app.services.foo",
            error_type="ValueError",
            message="bad thing",
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        out = tmp_path / f"errors-{today}.jsonl"
        assert out.exists()
        lines = out.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["service"] == "backend"
        assert rec["level"] == "error"
        assert rec["module"] == "app.services.foo"
        assert rec["error_type"] == "ValueError"
        assert rec["message"] == "bad thing"
        assert "timestamp" in rec

    def test_optional_fields_round_trip(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        el.log_error(
            service="backend",
            message="boom",
            traceback="Traceback\n  at foo.py:1\n",
            request_context={"method": "GET", "path": "/api/x"},
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rec = json.loads(
            (tmp_path / f"errors-{today}.jsonl").read_text().splitlines()[0]
        )
        assert rec["traceback"].startswith("Traceback")
        assert rec["request_context"] == {"method": "GET", "path": "/api/x"}

    def test_appends_multiple_entries(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        for i in range(3):
            el.log_error(service="backend", message=f"err-{i}")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = (tmp_path / f"errors-{today}.jsonl").read_text().splitlines()
        assert len(lines) == 3

    def test_os_error_on_write_does_not_raise(self, tmp_path, monkeypatch, caplog):
        """When the OS refuses the write (disk full, perms), log_error must
        degrade to a warning — errors must never cascade into app crashes."""
        el = ErrorLogger(errors_dir=tmp_path)

        def broken_open(*_a, **_kw):
            raise OSError("simulated disk full")

        monkeypatch.setattr("pathlib.Path.open", broken_open)

        # Should not raise.
        el.log_error(service="backend", message="won't get written")

    def test_default_arguments_produce_sensible_defaults(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        el.log_error(service="backend")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rec = json.loads(
            (tmp_path / f"errors-{today}.jsonl").read_text().splitlines()[0]
        )
        assert rec["level"] == "error"
        assert rec["module"] == "unknown"
        assert rec["error_type"] == "Exception"
        assert rec["message"] == ""


# ---------------------------------------------------------------------------
# read_errors
# ---------------------------------------------------------------------------

class TestReadErrors:
    def test_missing_file_returns_empty_list(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        assert el.read_errors(date="2020-01-01") == []

    def test_default_date_is_today(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        el.log_error(service="backend", message="today-err")
        got = el.read_errors()
        assert len(got) == 1
        assert got[0]["message"] == "today-err"

    def test_explicit_date_reads_that_file(self, tmp_path):
        # Hand-craft yesterday's file.
        (tmp_path / "errors-2024-01-01.jsonl").write_text(
            json.dumps({"service": "backend", "message": "old"}) + "\n",
            encoding="utf-8",
        )
        el = ErrorLogger(errors_dir=tmp_path)
        got = el.read_errors(date="2024-01-01")
        assert got == [{"service": "backend", "message": "old"}]

    def test_offset_and_limit_slice_entries(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        for i in range(5):
            el.log_error(service="backend", message=f"e{i}")
        got = el.read_errors(limit=2, offset=1)
        assert len(got) == 2
        assert got[0]["message"] == "e1"
        assert got[1]["message"] == "e2"

    def test_skips_empty_lines_and_bad_json(self, tmp_path):
        bad = (
            json.dumps({"service": "backend", "message": "good-1"}) + "\n"
            "\n"                         # empty line
            "{not json at all}\n"        # malformed
            + json.dumps({"service": "backend", "message": "good-2"}) + "\n"
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        (tmp_path / f"errors-{today}.jsonl").write_text(bad, encoding="utf-8")
        el = ErrorLogger(errors_dir=tmp_path)
        got = el.read_errors()
        assert [r["message"] for r in got] == ["good-1", "good-2"]


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

class TestRotate:
    def test_deletes_files_older_than_retention(self, tmp_path):
        # Create one old and one fresh file.
        old_date = (datetime.now(UTC) - timedelta(days=45)).strftime("%Y-%m-%d")
        new_date = datetime.now(UTC).strftime("%Y-%m-%d")
        (tmp_path / f"errors-{old_date}.jsonl").write_text("{}\n")
        (tmp_path / f"errors-{new_date}.jsonl").write_text("{}\n")

        el = ErrorLogger(errors_dir=tmp_path)
        deleted = el.rotate(retention_days=30)
        assert deleted == 1
        assert not (tmp_path / f"errors-{old_date}.jsonl").exists()
        assert (tmp_path / f"errors-{new_date}.jsonl").exists()

    def test_skips_files_with_unparseable_date(self, tmp_path, caplog):
        # This file won't match the date regex — rotate must tolerate it.
        (tmp_path / "errors-not-a-date.jsonl").write_text("{}\n")
        old_date = (datetime.now(UTC) - timedelta(days=45)).strftime("%Y-%m-%d")
        (tmp_path / f"errors-{old_date}.jsonl").write_text("{}\n")

        el = ErrorLogger(errors_dir=tmp_path)
        deleted = el.rotate(retention_days=30)
        # Only the dated one is reaped. Non-date stays in place.
        assert deleted == 1
        assert (tmp_path / "errors-not-a-date.jsonl").exists()

    def test_noop_when_no_files_match_retention_window(self, tmp_path):
        new_date = datetime.now(UTC).strftime("%Y-%m-%d")
        (tmp_path / f"errors-{new_date}.jsonl").write_text("{}\n")
        el = ErrorLogger(errors_dir=tmp_path)
        assert el.rotate(retention_days=30) == 0

    def test_noop_on_empty_directory(self, tmp_path):
        el = ErrorLogger(errors_dir=tmp_path)
        assert el.rotate(retention_days=30) == 0
