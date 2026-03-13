"""Pydantic schema exports.

All request/response schemas are re-exported here for convenience.
"""

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
from app.schemas.github import (
    GitHubUserInfo,
    LinkedRepoResponse,
    LinkRepoRequest,
    RepoInfo,
)
from app.schemas.mcp_models import (
    BatchDeleteResult,
    BranchesResult,
    BranchItem,
    DeleteResult,
    FeedbackSubmitResult,
    GitHubCodeMatch,
    GitHubFileContent,
    GitHubRepoItem,
    GitHubSearchResult,
    MCPError,
    OptimizationRecord,
    PaginationEnvelope,
    PipelineResult,
    RestoreResult,
    StatsResult,
)
from app.schemas.optimization import (
    HistoryStatsResponse,
    OptimizeRequest,
    PatchOptimizationRequest,
    RetryRequest,
)
from app.schemas.pipeline_outputs import (
    AnalyzeOutput,
    CodeSnippet,
    ExploreSynthesisOutput,
    IntentClassificationOutput,
    OptimizeFallbackOutput,
    StrategyOutput,
    ValidateOutput,
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
    # MCP structured output models
    "BatchDeleteResult",
    "BranchesResult",
    "BranchItem",
    "DeleteResult",
    "FeedbackSubmitResult",
    "GitHubCodeMatch",
    "GitHubFileContent",
    "GitHubRepoItem",
    "GitHubSearchResult",
    "MCPError",
    "OptimizationRecord",
    "PaginationEnvelope",
    "PipelineResult",
    "RestoreResult",
    "StatsResult",
    # Pipeline output schemas
    "IntentClassificationOutput",
    "CodeSnippet",
    "ExploreSynthesisOutput",
    "AnalyzeOutput",
    "StrategyOutput",
    "ValidateOutput",
    "OptimizeFallbackOutput",
]
