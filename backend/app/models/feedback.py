"""Feedback and user adaptation ORM models."""

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, Text, UniqueConstraint

from app.database import Base, utcnow


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    optimization_id = Column(Text, ForeignKey("optimizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Text, nullable=False)
    rating = Column(SmallInteger, nullable=False)  # -1, 0, +1
    dimension_overrides = Column(Text, nullable=True)  # JSON
    corrected_issues = Column(Text, nullable=True)  # JSON array
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("optimization_id", "user_id", name="uq_feedback_opt_user"),
        Index("ix_feedback_user_created", "user_id", "created_at"),
        Index("ix_feedback_optimization_id", "optimization_id"),
    )


class UserAdaptation(Base):
    __tablename__ = "user_adaptation"

    user_id = Column(Text, primary_key=True)
    dimension_weights = Column(Text, nullable=True)  # JSON
    strategy_affinities = Column(Text, nullable=True)  # JSON
    retry_threshold = Column(Float, default=5.0)
    feedback_count = Column(Integer, default=0)
    last_computed_at = Column(DateTime, default=utcnow)
    issue_frequency = Column(Text, nullable=True)  # JSON: {issue_id: count}
    adaptation_version = Column(Integer, default=0)
    damping_level = Column(Float, default=0.15)
    consistency_score = Column(Float, default=0.5)

    def __init__(self, **kwargs):
        kwargs.setdefault("adaptation_version", 0)
        kwargs.setdefault("damping_level", 0.15)
        kwargs.setdefault("consistency_score", 0.5)
        super().__init__(**kwargs)
