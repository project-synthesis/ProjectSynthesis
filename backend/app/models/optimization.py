import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.database import Base

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


class Optimization(Base):
    __tablename__ = "optimizations"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Core prompt data
    raw_prompt = Column(Text, nullable=False)
    optimized_prompt = Column(Text, nullable=True)

    # Analysis results
    task_type = Column(Text, nullable=True)
    complexity = Column(Text, nullable=True)
    weaknesses = Column(Text, nullable=True)  # JSON array
    strengths = Column(Text, nullable=True)  # JSON array
    changes_made = Column(Text, nullable=True)  # JSON array
    analysis_quality = Column(String(20), nullable=True)  # "full" | "fallback" | "degraded" | "failed"

    # Strategy results
    primary_framework = Column(Text, nullable=True)
    secondary_frameworks = Column(Text, nullable=True)  # JSON array
    approach_notes = Column(Text, nullable=True)
    framework_applied = Column(Text, nullable=True)
    optimization_notes = Column(Text, nullable=True)
    strategy_rationale = Column(Text, nullable=True)
    strategy_source = Column(Text, nullable=True)  # "llm" | "llm_json" | "heuristic" | "override"

    # Validation scores (1-10)
    clarity_score = Column(Integer, nullable=True)
    specificity_score = Column(Integer, nullable=True)
    structure_score = Column(Integer, nullable=True)
    faithfulness_score = Column(Integer, nullable=True)
    conciseness_score = Column(Integer, nullable=True)
    overall_score = Column(Integer, nullable=True)

    # Validation verdict
    is_improvement = Column(Boolean, nullable=True)
    verdict = Column(Text, nullable=True)
    issues = Column(Text, nullable=True)  # JSON array
    validation_quality = Column(String(20), nullable=True)  # "full" | "fallback" | "degraded" | "failed"

    # Timing & provider
    duration_ms = Column(Integer, nullable=True)
    provider_used = Column(Text, nullable=True)
    model_explore = Column(Text, nullable=True)
    model_analyze = Column(Text, nullable=True)
    model_strategy = Column(Text, nullable=True)
    model_optimize = Column(Text, nullable=True)
    model_validate = Column(Text, nullable=True)

    # Status
    status = Column(Text, default="completed", nullable=False)
    error_message = Column(Text, nullable=True)

    # Attribution
    user_id = Column(Text, nullable=True)   # authenticated user who created this

    # Soft-delete
    deleted_at = Column(DateTime, nullable=True)

    # Organization
    project = Column(Text, nullable=True)
    tags = Column(Text, default="[]")  # JSON array
    title = Column(Text, nullable=True)
    version = Column(Text, nullable=True)
    retry_of = Column(Text, ForeignKey("optimizations.id", ondelete="SET NULL"), nullable=True)
    row_version = Column(Integer, nullable=False, server_default="0", default=0)

    # GitHub / codebase context
    linked_repo_full_name = Column(Text, nullable=True)
    linked_repo_branch = Column(Text, nullable=True)
    codebase_context_snapshot = Column(Text, nullable=True)  # JSON

    # ── JSON-as-TEXT columns ────────────────────────────────────────────
    # weaknesses, strengths, changes_made, issues, tags, secondary_frameworks
    # are stored as JSON-encoded TEXT. At current scale (< 10K rows),
    # application-level deserialization is acceptable. Upgrade paths:
    #   SQLite:     json_extract(col, '$') + json_each() for membership tests
    #   PostgreSQL: migrate to JSONB columns; use @> containment operator
    #   Junction:   tags -> optimization_tags (id, tag) for heavy filtering
    # ────────────────────────────────────────────────────────────────────

    __table_args__ = (
        Index("idx_optimizations_project", "project"),
        Index("idx_optimizations_task_type", "task_type"),
        Index("idx_optimizations_created_at", created_at.desc()),
        Index("idx_optimizations_user_id", "user_id"),
        Index("idx_optimizations_retry_of", "retry_of"),
        Index("idx_optimizations_user_listing",
              "user_id", "deleted_at", created_at.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        result = {}
        for col in self.__table__.columns:
            value = getattr(self, col.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            # Parse JSON fields
            if col.name in ("weaknesses", "strengths", "changes_made", "issues", "tags", "secondary_frameworks"):
                if value and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "Malformed JSON in %s.%s (id=%s), returning empty list",
                            self.__tablename__, col.name, self.id,
                        )
                        value = []
            result[col.name] = value
        return result
