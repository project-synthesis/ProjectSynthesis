import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, LargeBinary, Text

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class GitHubToken(Base):
    __tablename__ = "github_tokens"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(Text, nullable=False)
    github_user_id = Column(Integer, nullable=False)
    github_login = Column(Text, nullable=False)
    token_encrypted = Column(LargeBinary, nullable=False)
    token_type = Column(Text, nullable=False)  # always "github_app"
    refresh_token_encrypted = Column(LargeBinary, nullable=True)
    refresh_token_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)  # always set (now + 8h)
    avatar_url = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_github_tokens_session", "session_id"),
    )


class LinkedRepo(Base):
    __tablename__ = "linked_repos"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False)
    branch = Column(Text, nullable=False, default="main")
    default_branch = Column(Text, nullable=True)
    language = Column(Text, nullable=True)
    linked_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_linked_repos_session", "session_id"),
    )
