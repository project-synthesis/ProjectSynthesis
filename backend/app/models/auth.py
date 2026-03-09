"""Auth models: User and RefreshToken tables."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, Text

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"
    moderator = "moderator"


class User(Base):
    __tablename__ = "users"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    github_user_id = Column(Integer, nullable=False, unique=True)
    github_login = Column(Text, nullable=False)
    role = Column(
        Enum(UserRole, native_enum=False, length=32),
        nullable=False,
        default=UserRole.user,
    )
    # Profile fields (nullable — populated progressively)
    email = Column(Text, nullable=True)
    avatar_url = Column(Text, nullable=True)
    display_name = Column(Text, nullable=True)
    onboarding_completed_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_users_github_user_id", "github_user_id"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)
    device_id = Column(Text, nullable=True)  # per-device revocation; NULL for legacy tokens
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_refresh_tokens_user_id", "user_id"),
        Index("idx_refresh_tokens_token_hash", "token_hash"),
        Index("idx_refresh_tokens_device_id", "device_id"),
    )
