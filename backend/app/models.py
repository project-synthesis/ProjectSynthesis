"""SQLAlchemy models — all tables for the application."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, backref, relationship


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
    models_by_phase = Column(JSON, nullable=True)
    scoring_mode = Column(String, nullable=True)  # independent / self_rated
    duration_ms = Column(Integer, nullable=True)
    repo_full_name = Column(String, nullable=True)
    codebase_context_snapshot = Column(Text, nullable=True)
    status = Column(String, default="completed", nullable=False)  # completed / failed / interrupted
    routing_tier = Column(String, nullable=True)  # internal / sampling / passthrough
    trace_id = Column(String, nullable=True)
    tokens_total = Column(Integer, nullable=True)
    tokens_by_phase = Column(JSON, nullable=True)
    context_sources = Column(JSON, nullable=True)
    original_scores = Column(JSON, nullable=True)
    score_deltas = Column(JSON, nullable=True)
    intent_label = Column(String, nullable=True)
    domain = Column(String, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
    optimized_embedding = Column(LargeBinary, nullable=True)
    transformation_embedding = Column(LargeBinary, nullable=True)
    cluster_id = Column(String, ForeignKey("prompt_cluster.id"), nullable=True)
    domain_raw = Column(String, nullable=True)
    heuristic_flags = Column(JSON, nullable=True)
    suggestions = Column(JSON, nullable=True)


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


class PromptCluster(Base):
    """Unified prompt cluster — replaces PatternFamily + TaxonomyNode."""
    __tablename__ = "prompt_cluster"

    id = Column(String, primary_key=True, default=_uuid)
    parent_id = Column(String, ForeignKey("prompt_cluster.id"), nullable=True, index=True)
    label = Column(String, nullable=False, default="")
    state = Column(String(20), nullable=False, default="active")  # candidate|active|mature|template|domain|archived
    domain = Column(String(50), nullable=False, default="general")
    task_type = Column(String(50), nullable=False, default="general")

    centroid_embedding = Column(LargeBinary, nullable=True)
    member_count = Column(Integer, nullable=False, default=0)
    weighted_member_sum = Column(Float, default=0.0, nullable=False, server_default="0.0")
    scored_count = Column(Integer, nullable=False, default=0)
    usage_count = Column(Integer, nullable=False, default=0)
    avg_score = Column(Float, nullable=True)

    coherence = Column(Float, nullable=True)
    separation = Column(Float, nullable=True)
    stability = Column(Float, nullable=True, default=0.0)
    persistence = Column(Float, nullable=True, default=0.5)

    umap_x = Column(Float, nullable=True)
    umap_y = Column(Float, nullable=True)
    umap_z = Column(Float, nullable=True)
    color_hex = Column(String(7), nullable=True)

    preferred_strategy = Column(String(50), nullable=True)
    cluster_metadata = Column(JSON, nullable=True)
    prune_flag_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)
    promoted_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    children = relationship("PromptCluster", backref=backref("parent", remote_side="PromptCluster.id"), lazy="select")
    meta_patterns = relationship("MetaPattern", back_populates="cluster", lazy="select")

    __table_args__ = (
        Index("ix_prompt_cluster_state", "state"),
        Index("ix_prompt_cluster_domain_state", "domain", "state"),
        Index("ix_prompt_cluster_state_label", "state", "label"),
        Index("ix_prompt_cluster_persistence", "persistence"),
        Index("ix_prompt_cluster_created_at", created_at.desc()),
        Index(
            "uq_prompt_cluster_domain_label",
            "label",
            unique=True,
            sqlite_where=text("state = 'domain'"),
        ),
    )



class MetaPattern(Base):
    __tablename__ = "meta_patterns"

    id = Column(String, primary_key=True, default=_uuid)
    cluster_id = Column(String, ForeignKey("prompt_cluster.id"), nullable=False, index=True)
    pattern_text = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    source_count = Column(Integer, default=1, nullable=False)
    global_source_count = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    cluster = relationship("PromptCluster", back_populates="meta_patterns")


class OptimizationPattern(Base):
    __tablename__ = "optimization_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    optimization_id = Column(String, ForeignKey("optimizations.id"), nullable=False)
    cluster_id = Column(String, ForeignKey("prompt_cluster.id"), nullable=False)
    meta_pattern_id = Column(String, ForeignKey("meta_patterns.id"), nullable=True)
    relationship = Column(String(20), nullable=False, default="source")
    similarity = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_optimization_pattern_opt_rel", "optimization_id", "relationship"),
        Index("ix_optimization_pattern_cluster", "cluster_id"),
    )


class TaxonomySnapshot(Base):
    __tablename__ = "taxonomy_snapshots"

    id = Column(String, primary_key=True, default=_uuid)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    trigger = Column(String, nullable=False)  # 'warm_path' | 'cold_path' | 'manual'

    # System-wide metrics
    q_system = Column(Float, nullable=False)
    q_coherence = Column(Float, nullable=False)
    q_separation = Column(Float, nullable=False)
    q_coverage = Column(Float, nullable=False)
    q_dbcv = Column(Float, default=0.0, nullable=False)

    # What changed
    operations = Column(Text, default="[]", nullable=False)  # JSON list
    nodes_created = Column(Integer, default=0, nullable=False)
    nodes_retired = Column(Integer, default=0, nullable=False)
    nodes_merged = Column(Integer, default=0, nullable=False)
    nodes_split = Column(Integer, default=0, nullable=False)

    # Recovery
    tree_state = Column(Text, nullable=True)  # JSON: node IDs + parent edges

    # Legacy flag — marks snapshots from pre-PromptCluster era
    legacy = Column(Boolean, nullable=False, default=False)


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


# --- Security audit trail ---

class AuditLog(Base):
    """Security audit trail for sensitive operations."""
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=_uuid)
    timestamp = Column(DateTime, default=_utcnow, nullable=False, index=True)
    action = Column(String, nullable=False, index=True)
    actor_ip = Column(String, nullable=True)
    actor_session = Column(String, nullable=True)
    detail = Column(JSON, nullable=True)
    outcome = Column(String, nullable=False, default="success")
