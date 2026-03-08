import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, Text

from app.database import Base


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

    # Organization
    project = Column(Text, nullable=True)
    tags = Column(Text, default="[]")  # JSON array
    title = Column(Text, nullable=True)
    version = Column(Text, nullable=True)
    retry_of = Column(Text, nullable=True)  # FK -> optimizations.id

    # GitHub / codebase context
    linked_repo_full_name = Column(Text, nullable=True)
    linked_repo_branch = Column(Text, nullable=True)
    codebase_context_snapshot = Column(Text, nullable=True)  # JSON

    __table_args__ = (
        Index("idx_optimizations_project", "project"),
        Index("idx_optimizations_task_type", "task_type"),
        Index("idx_optimizations_created_at", created_at.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
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
                        pass
            result[col.name] = value
        return result
