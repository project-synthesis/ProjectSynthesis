"""Tests for feedback-related SQLAlchemy models."""

from sqlalchemy import inspect

from app.models.adaptation_event import AdaptationEvent
from app.models.feedback import UserAdaptation
from app.models.framework_performance import FrameworkPerformance


def test_user_adaptation_has_issue_frequency_column():
    columns = {c.name for c in inspect(UserAdaptation).columns}
    assert "issue_frequency" in columns


def test_user_adaptation_has_adaptation_version_column():
    columns = {c.name for c in inspect(UserAdaptation).columns}
    assert "adaptation_version" in columns


def test_user_adaptation_has_damping_columns():
    columns = {c.name for c in inspect(UserAdaptation).columns}
    assert "damping_level" in columns
    assert "consistency_score" in columns


def test_user_adaptation_defaults():
    ua = UserAdaptation(user_id="test-user")
    assert ua.adaptation_version == 0
    assert ua.damping_level == 0.15
    assert ua.consistency_score == 0.5
    assert ua.issue_frequency is None


def test_framework_performance_unique_constraint():
    table = FrameworkPerformance.__table__
    uq = next(
        (c for c in table.constraints if getattr(c, "name", None) == "uq_fw_perf_user_task_fw"),
        None,
    )
    assert uq is not None, "unique constraint uq_fw_perf_user_task_fw not found"
    col_names = {c.name for c in uq.columns}
    assert col_names == {"user_id", "task_type", "framework"}


def test_framework_performance_has_all_columns():
    columns = {c.name for c in inspect(FrameworkPerformance).columns}
    expected = {
        "id", "user_id", "task_type", "framework", "avg_scores",
        "user_rating_avg", "issue_frequency", "sample_count",
        "elasticity_snapshot", "last_updated",
    }
    assert expected.issubset(columns)


def test_framework_performance_defaults():
    fp = FrameworkPerformance(
        id="test-id", user_id="test-user",
        task_type="coding", framework="chain-of-thought",
    )
    assert fp.user_rating_avg == 0.0
    assert fp.sample_count == 0


def test_adaptation_event_has_index():
    indexes = {idx.name for idx in AdaptationEvent.__table__.indexes}
    assert "ix_adaptation_events_user_created" in indexes


def test_adaptation_event_construction():
    evt = AdaptationEvent(
        id="test-evt", user_id="test-user", event_type="recomputed",
    )
    assert evt.event_type == "recomputed"
    assert evt.user_id == "test-user"
    # created_at uses a server default; verify column is defined
    col = AdaptationEvent.__table__.c.created_at
    assert col.default is not None
