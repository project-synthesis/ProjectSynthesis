"""Framework performance tracking model — per-user, per-task, per-framework."""

import uuid

from sqlalchemy import Column, DateTime, Float, Index, Integer, Text, UniqueConstraint

from app.database import Base, utcnow


class FrameworkPerformance(Base):
    __tablename__ = "framework_performance"
    __table_args__ = (
        UniqueConstraint("user_id", "task_type", "framework", name="uq_fw_perf_user_task_fw"),
        Index("ix_framework_perf_user_task", "user_id", "task_type"),
    )
    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Text, nullable=False)
    task_type = Column(Text, nullable=False)
    framework = Column(Text, nullable=False)
    avg_scores = Column(Text, nullable=True)  # JSON: {dimension: avg_score}
    user_rating_avg = Column(Float, default=0.0)
    issue_frequency = Column(Text, nullable=True)  # JSON: {issue_id: count}
    sample_count = Column(Integer, default=0)
    elasticity_snapshot = Column(Text, nullable=True)  # JSON: {dimension: elasticity}
    last_updated = Column(DateTime, default=utcnow)

    def __init__(self, **kwargs):
        # Pre-flush visibility: Column defaults only apply at flush time.
        # Services read these values before commit, so set them eagerly.
        kwargs.setdefault("user_rating_avg", 0.0)
        kwargs.setdefault("sample_count", 0)
        super().__init__(**kwargs)
