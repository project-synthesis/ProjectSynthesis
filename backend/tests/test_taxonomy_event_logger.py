"""Tests for TaxonomyEventLogger — JSONL persistence + ring buffer."""

import json
from pathlib import Path

import pytest

from app.services.taxonomy.event_logger import TaxonomyEventLogger


@pytest.fixture
def logger(tmp_path: Path) -> TaxonomyEventLogger:
    return TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)


class TestLogDecision:
    def test_writes_jsonl_file(self, logger: TaxonomyEventLogger, tmp_path: Path) -> None:
        logger.log_decision(
            path="hot", op="assign", decision="merge_into",
            cluster_id="c1", context={"raw_score": 0.72},
        )
        files = list(tmp_path.glob("decisions-*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text().strip()
        event = json.loads(line)
        assert event["path"] == "hot"
        assert event["op"] == "assign"
        assert event["decision"] == "merge_into"
        assert event["cluster_id"] == "c1"
        assert event["context"]["raw_score"] == 0.72
        assert "ts" in event

    def test_appends_to_ring_buffer(self, logger: TaxonomyEventLogger) -> None:
        logger.log_decision(path="warm", op="phase", decision="accepted", context={})
        recent = logger.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0]["op"] == "phase"

    def test_ring_buffer_capped(self, tmp_path: Path) -> None:
        small_logger = TaxonomyEventLogger(
            events_dir=tmp_path, publish_to_bus=False, buffer_size=5,
        )
        for i in range(10):
            small_logger.log_decision(
                path="hot", op="assign", decision="create_new",
                context={"idx": i},
            )
        recent = small_logger.get_recent(limit=20)
        assert len(recent) == 5
        # Oldest should be idx=5 (first 5 evicted)
        assert recent[-1]["context"]["idx"] == 5


class TestGetRecent:
    def test_filter_by_path(self, logger: TaxonomyEventLogger) -> None:
        logger.log_decision(path="hot", op="assign", decision="merge_into", context={})
        logger.log_decision(path="warm", op="phase", decision="accepted", context={})
        assert len(logger.get_recent(path="hot")) == 1
        assert len(logger.get_recent(path="warm")) == 1

    def test_filter_by_op(self, logger: TaxonomyEventLogger) -> None:
        logger.log_decision(path="warm", op="split", decision="success", context={})
        logger.log_decision(path="warm", op="merge", decision="success", context={})
        assert len(logger.get_recent(op="split")) == 1

    def test_limit(self, logger: TaxonomyEventLogger) -> None:
        for _ in range(10):
            logger.log_decision(path="hot", op="assign", decision="create_new", context={})
        assert len(logger.get_recent(limit=3)) == 3


class TestGetHistory:
    def test_reads_from_jsonl(self, logger: TaxonomyEventLogger) -> None:
        logger.log_decision(path="cold", op="refit", decision="accepted", context={})
        from datetime import UTC, datetime
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        history = logger.get_history(date=today)
        assert len(history) == 1
        assert history[0]["op"] == "refit"

    def test_pagination(self, logger: TaxonomyEventLogger) -> None:
        for i in range(5):
            logger.log_decision(
                path="hot", op="assign", decision="create_new", context={"i": i},
            )
        from datetime import UTC, datetime
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        page1 = logger.get_history(date=today, limit=2, offset=0)
        page2 = logger.get_history(date=today, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2

    def test_missing_date_returns_empty(self, logger: TaxonomyEventLogger) -> None:
        assert logger.get_history(date="1999-01-01") == []


class TestRotate:
    def test_deletes_old_files(self, tmp_path: Path) -> None:
        old_file = tmp_path / "decisions-2020-01-01.jsonl"
        old_file.write_text('{"test": true}\n')
        logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
        deleted = logger.rotate(retention_days=1)
        assert deleted == 1
        assert not old_file.exists()

    def test_keeps_recent_files(self, logger: TaxonomyEventLogger, tmp_path: Path) -> None:
        logger.log_decision(path="hot", op="assign", decision="merge_into", context={})
        deleted = logger.rotate(retention_days=1)
        assert deleted == 0
        assert len(list(tmp_path.glob("*.jsonl"))) == 1
