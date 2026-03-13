"""Pydantic schema exports.

All request/response schemas are re-exported here for convenience.
"""

from app.schemas.github import (
    GitHubUserInfo,
    LinkedRepoResponse,
    LinkRepoRequest,
    RepoInfo,
)
from app.schemas.feedback import (
    AdaptationStateResponse,
    DimensionDelta,
    FeedbackAggregate,
    FeedbackCreate,
    FeedbackResponse,
    FeedbackStatsResponse,
    FeedbackWithAggregate,
    InstructionCompliance,
    RetryHistoryEntry,
)
from app.schemas.optimization import (
    HistoryStatsResponse,
    OptimizeRequest,
    PatchOptimizationRequest,
    RetryRequest,
)
from app.schemas.refinement import (
    BranchCompareResponse,
    BranchListResponse,
    BranchResponse,
    ForkRequest,
    RefineRequest,
    SelectRequest,
)

__all__ = [
    # Optimization schemas
    "OptimizeRequest",
    "PatchOptimizationRequest",
    "RetryRequest",
    "HistoryStatsResponse",
    # GitHub schemas
    "RepoInfo",
    "LinkRepoRequest",
    "LinkedRepoResponse",
    "GitHubUserInfo",
    # H3: Quality feedback loops
    "AdaptationStateResponse",
    "DimensionDelta",
    "FeedbackAggregate",
    "FeedbackCreate",
    "FeedbackResponse",
    "FeedbackStatsResponse",
    "FeedbackWithAggregate",
    "InstructionCompliance",
    "RetryHistoryEntry",
    "BranchCompareResponse",
    "BranchListResponse",
    "BranchResponse",
    "ForkRequest",
    "RefineRequest",
    "SelectRequest",
]
