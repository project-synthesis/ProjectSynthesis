"""Adaptation audit trail — 90-day retention, purged during recompute."""

import uuid

from sqlalchemy import Column, DateTime, Index, Text

from app.database import Base, utcnow


class AdaptationEvent(Base):
    __tablename__ = "adaptation_events"
    __table_args__ = (
        Index("ix_adaptation_events_user_created", "user_id", "created_at"),
    )
    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    payload = Column(Text, nullable=True)  # JSON: event-specific data dict
    created_at = Column(DateTime, default=utcnow, nullable=False)
