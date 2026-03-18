"""SQLAlchemy models — all tables for the application."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# --- Core tables (Section 6) ---

class Optimization(Base):
    __tablename__ = "optimizations"

    id = Column(String, primary_key=True, default=_uuid)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    raw_prompt = Column(Text, nullable=False)
    optimized_prompt = Column(Text, nullable=True)
    task_type = Column(String, nullable=True)
    strategy_used = Column(String, nullable=True)
    changes_summary = Column(Text, nullable=True)
    score_clarity = Column(Float, nullable=True)
    score_specificity = Column(Float, nullable=True)
    score_structure = Column(Float, nullable=True)
    score_faithfulness = Column(Float, nullable=True)
    score_conciseness = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    provider = Column(String, nullable=True)
    model_used = Column(String, nullable=True)
    scoring_mode = Column(String, nullable=True)  # independent / self_rated
    duration_ms = Column(Integer, nullable=True)
    repo_full_name = Column(String, nullable=True)
    codebase_context_snapshot = Column(Text, nullable=True)
    status = Column(String, default="completed", nullable=False)  # completed / failed / interrupted
    trace_id = Column(String, nullable=True)
    tokens_total = Column(Integer, nullable=True)
    tokens_by_phase = Column(JSON, nullable=True)
    context_sources = Column(JSON, nullable=True)
    original_scores = Column(JSON, nullable=True)
    score_deltas = Column(JSON, nullable=True)
    intent_label = Column(String, nullable=True)
    domain = Column(String, nullable=True)
    embedding = Column(LargeBinary, nullable=True)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(String, primary_key=True, default=_uuid)
    optimization_id = Column(String, ForeignKey("optimizations.id"), nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    rating = Column(String, nullable=False)  # thumbs_up / thumbs_down
    comment = Column(Text, nullable=True)


class StrategyAffinity(Base):
    __tablename__ = "strategy_affinities"

    id = Column(String, primary_key=True, default=_uuid)
    task_type = Column(String, nullable=False)
    strategy = Column(String, nullable=False)
    thumbs_up = Column(Integer, default=0, nullable=False)
    thumbs_down = Column(Integer, default=0, nullable=False)
    approval_rate = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class PatternFamily(Base):
    __tablename__ = "pattern_families"

    id = Column(String, primary_key=True, default=_uuid)
    intent_label = Column(String, nullable=False)
    domain = Column(String, nullable=False, default="general")
    task_type = Column(String, nullable=False, default="general")
    centroid_embedding = Column(LargeBinary, nullable=False)
    usage_count = Column(Integer, default=0, nullable=False)
    member_count = Column(Integer, default=1, nullable=False)
    avg_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class MetaPattern(Base):
    __tablename__ = "meta_patterns"

    id = Column(String, primary_key=True, default=_uuid)
    family_id = Column(String, ForeignKey("pattern_families.id"), nullable=False)
    pattern_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    source_count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class OptimizationPattern(Base):
    __tablename__ = "optimization_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    optimization_id = Column(String, ForeignKey("optimizations.id"), nullable=False)
    family_id = Column(String, ForeignKey("pattern_families.id"), nullable=False)
    meta_pattern_id = Column(String, ForeignKey("meta_patterns.id"), nullable=True)
    relationship = Column(String, nullable=False)  # "source" or "applied"
    created_at = Column(DateTime, default=_utcnow, nullable=False)


# --- Ported tables (GitHub/Embedding) ---
# These match the v2 schema closely to minimize friction when porting services in Phase 2.

class GitHubToken(Base):
    __tablename__ = "github_tokens"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, nullable=False, unique=True)
    token_encrypted = Column(LargeBinary, nullable=False)  # Fernet-encrypted, matches v2
    token_type = Column(String, default="oauth", nullable=False)
    github_user_id = Column(String, nullable=True)
    github_login = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    refresh_token_encrypted = Column(LargeBinary, nullable=True)
    refresh_token_expires_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class LinkedRepo(Base):
    __tablename__ = "linked_repos"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, nullable=False)
    full_name = Column(String, nullable=False)  # matches v2 column name
    default_branch = Column(String, default="main", nullable=False)
    branch = Column(String, nullable=True)  # active working branch (distinct from default)
    language = Column(String, nullable=True)
    linked_at = Column(DateTime, default=_utcnow, nullable=False)  # matches v2 column name


class RepoFileIndex(Base):
    __tablename__ = "repo_file_index"

    id = Column(String, primary_key=True, default=_uuid)
    repo_full_name = Column(String, nullable=False, index=True)
    branch = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_sha = Column(String, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    outline = Column(Text, nullable=True)
    embedding = Column(LargeBinary, nullable=True)  # numpy bytes (384*4=1536), matches v2
    updated_at = Column(DateTime, default=_utcnow, nullable=False)


class RepoIndexMeta(Base):
    __tablename__ = "repo_index_meta"

    id = Column(String, primary_key=True, default=_uuid)
    repo_full_name = Column(String, nullable=False)
    branch = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)
    file_count = Column(Integer, default=0, nullable=False)
    head_sha = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


# --- Refinement tables (Section 13) ---
# RefinementBranch defined first since RefinementTurn has FK to it.

class RefinementBranch(Base):
    __tablename__ = "refinement_branches"

    id = Column(String, primary_key=True, default=_uuid)
    optimization_id = Column(String, ForeignKey("optimizations.id"), nullable=False)
    parent_branch_id = Column(String, nullable=True)
    forked_at_version = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class RefinementTurn(Base):
    __tablename__ = "refinement_turns"

    id = Column(String, primary_key=True, default=_uuid)
    optimization_id = Column(String, ForeignKey("optimizations.id"), nullable=False)
    version = Column(Integer, nullable=False)
    branch_id = Column(String, ForeignKey("refinement_branches.id"), nullable=False)
    parent_version = Column(Integer, nullable=True)
    refinement_request = Column(Text, nullable=True)
    prompt = Column(Text, nullable=False)
    scores = Column(JSON, nullable=True)
    deltas = Column(JSON, nullable=True)
    deltas_from_original = Column(JSON, nullable=True)
    strategy_used = Column(String, nullable=True)
    suggestions = Column(JSON, nullable=True)
    trace_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
