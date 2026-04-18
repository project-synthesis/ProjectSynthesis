"""SQLAlchemy models — all tables for the application.

Uses SQLAlchemy 2.0 ``Mapped[]`` typed declarative: class-level attributes
declare runtime types, and the ORM binds them to ``mapped_column(...)``.
Instance access returns the annotated Python type (not ``Column[X]``), which
eliminates the descriptor-typing drift that previously required dozens of
``# type: ignore`` comments across the service layer.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, backref, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# --- Core tables (Section 6) ---

class Optimization(Base):
    __tablename__ = "optimizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    raw_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    optimized_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[str | None] = mapped_column(String, nullable=True)
    strategy_used: Mapped[str | None] = mapped_column(String, nullable=True)
    changes_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_clarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_specificity: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_structure: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_faithfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_conciseness: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String, nullable=True)
    models_by_phase: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    scoring_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repo_full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    codebase_context_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="completed", nullable=False)
    routing_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_by_phase: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    context_sources: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    original_scores: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    score_deltas: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    intent_label: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    optimized_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    transformation_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    qualifier_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phase_weights_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String, ForeignKey("prompt_cluster.id"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("prompt_cluster.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    domain_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    heuristic_flags: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    improvement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggestions: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    optimization_id: Mapped[str] = mapped_column(String, ForeignKey("optimizations.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    rating: Mapped[str] = mapped_column(String, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class StrategyAffinity(Base):
    __tablename__ = "strategy_affinities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)
    thumbs_up: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    thumbs_down: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approval_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False,
    )


class PromptCluster(Base):
    """Unified prompt cluster — replaces PatternFamily + TaxonomyNode."""
    __tablename__ = "prompt_cluster"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    parent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("prompt_cluster.id"), nullable=True, index=True,
    )
    label: Mapped[str] = mapped_column(String, nullable=False, default="")
    # States: candidate|active|mature|domain|project|archived
    # (legacy 'template' still tolerated by read-side _LegacyClusterState — see services/taxonomy/event_logger.py)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, default="general")

    centroid_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    template_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    weighted_member_sum: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, server_default="0.0",
    )
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    coherence: Mapped[float | None] = mapped_column(Float, nullable=True)
    separation: Mapped[float | None] = mapped_column(Float, nullable=True)
    stability: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    persistence: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.5)

    umap_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    umap_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    umap_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    color_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)

    preferred_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cluster_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prune_flag_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow,
    )

    # Relationships
    children = relationship(
        "PromptCluster",
        backref=backref("parent", remote_side="PromptCluster.id"),
        lazy="select",
    )
    meta_patterns = relationship("MetaPattern", back_populates="cluster", lazy="select")

    __table_args__ = (
        Index("ix_prompt_cluster_state", "state"),
        Index("ix_prompt_cluster_domain_state", "domain", "state"),
        Index("ix_prompt_cluster_state_label", "state", "label"),
        Index("ix_prompt_cluster_persistence", "persistence"),
        Index("ix_prompt_cluster_created_at", created_at.desc()),
        # NOTE: Actual DB index uses COALESCE(parent_id, '') for NULL safety.
        # SQLAlchemy Index() can't express COALESCE — see migration e7f8a9b0c1d2.
        Index(
            "uq_prompt_cluster_domain_label",
            "parent_id",
            "label",
            unique=True,
            sqlite_where=text("state = 'domain'"),
        ),
    )


class MetaPattern(Base):
    __tablename__ = "meta_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    cluster_id: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_cluster.id"), nullable=False, index=True,
    )
    pattern_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    global_source_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    cluster = relationship("PromptCluster", back_populates="meta_patterns")


class OptimizationPattern(Base):
    __tablename__ = "optimization_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    optimization_id: Mapped[str] = mapped_column(
        String, ForeignKey("optimizations.id"), nullable=False,
    )
    cluster_id: Mapped[str] = mapped_column(
        String, ForeignKey("prompt_cluster.id"), nullable=False,
    )
    meta_pattern_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("meta_patterns.id"), nullable=True,
    )
    global_pattern_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("global_patterns.id"), nullable=True,
    )
    relationship: Mapped[str] = mapped_column(String(20), nullable=False, default="source")
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_optimization_pattern_opt_rel", "optimization_id", "relationship"),
        Index("ix_optimization_pattern_cluster", "cluster_id"),
    )


class TaxonomySnapshot(Base):
    __tablename__ = "taxonomy_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False)

    # System-wide metrics
    q_system: Mapped[float] = mapped_column(Float, nullable=False)
    q_coherence: Mapped[float] = mapped_column(Float, nullable=False)
    q_separation: Mapped[float] = mapped_column(Float, nullable=False)
    q_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    q_dbcv: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    q_health: Mapped[float | None] = mapped_column(Float, nullable=True)

    # What changed
    operations: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    nodes_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nodes_retired: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nodes_merged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nodes_split: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Recovery
    tree_state: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Legacy flag — marks snapshots from pre-PromptCluster era
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_taxonomy_snapshot_created_at", created_at.desc()),
    )


class GlobalPattern(Base):
    """Durable cross-project pattern (ADR-005).

    Promoted from MetaPattern when a technique proves universal across
    2+ projects. Survives source cluster archival. Injected into all
    projects with a 1.3x relevance boost.
    """

    __tablename__ = "global_patterns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    pattern_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_cluster_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_project_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cross_project_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    global_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_cluster_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    last_validated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


# --- Ported tables (GitHub/Embedding) ---
# These match the v2 schema closely to minimize friction when porting services in Phase 2.

class GitHubToken(Base):
    __tablename__ = "github_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_type: Mapped[str] = mapped_column(String, default="oauth", nullable=False)
    github_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    github_login: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False,
    )


class LinkedRepo(Base):
    __tablename__ = "linked_repos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    default_branch: Mapped[str] = mapped_column(String, default="main", nullable=False)
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    project_node_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("prompt_cluster.id"), nullable=True,
    )


class RepoFileIndex(Base):
    __tablename__ = "repo_file_index"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    outline: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index(
            "idx_repo_file_index_repo_branch_path",
            "repo_full_name", "branch", "file_path",
            unique=True,
        ),
    )


class RepoIndexMeta(Base):
    __tablename__ = "repo_index_meta"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False)
    branch: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    head_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    explore_synthesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthesis_status: Mapped[str] = mapped_column(
        String, default="pending", server_default="pending", nullable=False,
    )
    synthesis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    __table_args__ = (
        Index("idx_repo_index_meta_repo_branch", "repo_full_name", "branch", unique=True),
    )


# --- Refinement tables (Section 13) ---
# RefinementBranch defined first since RefinementTurn has FK to it.

class RefinementBranch(Base):
    __tablename__ = "refinement_branches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    optimization_id: Mapped[str] = mapped_column(
        String, ForeignKey("optimizations.id"), nullable=False,
    )
    parent_branch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    forked_at_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class RefinementTurn(Base):
    __tablename__ = "refinement_turns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    optimization_id: Mapped[str] = mapped_column(
        String, ForeignKey("optimizations.id"), nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_id: Mapped[str] = mapped_column(
        String, ForeignKey("refinement_branches.id"), nullable=False,
    )
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refinement_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deltas: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deltas_from_original: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    strategy_used: Mapped[str | None] = mapped_column(String, nullable=True)
    suggestions: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


# --- Security audit trail ---

class AuditLog(Base):
    """Security audit trail for sensitive operations."""
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    actor_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_session: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False, default="success")


class PromptTemplate(Base):
    """Immutable frozen template snapshot.

    See docs/superpowers/specs/2026-04-18-template-architecture-design.md
    §Architecture §Data model.
    """
    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)

    source_cluster_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompt_cluster.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    source_optimization_id: Mapped[str | None] = mapped_column(
        ForeignKey("optimizations.id", ondelete="SET NULL"), nullable=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompt_cluster.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    label: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    pattern_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    domain_label: Mapped[str] = mapped_column(String, nullable=False)

    promoted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    retired_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_template_source_optimization_live",
            "source_cluster_id", "source_optimization_id",
            unique=True,
            sqlite_where=text(
                "source_cluster_id IS NOT NULL "
                "AND source_optimization_id IS NOT NULL "
                "AND retired_at IS NULL"
            ),
        ),
        Index(
            "idx_template_project_domain_active",
            "project_id", "domain_label", "promoted_at",
            sqlite_where=text("retired_at IS NULL"),
        ),
    )
