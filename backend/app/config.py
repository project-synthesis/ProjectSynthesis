"""Application configuration via pydantic-settings."""

import secrets
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# MCP session detection windows (shared by mcp_server.py + health.py).
# Capability freshness: how long a session file's sampling_capable field is trusted
# during restart recovery (read from mcp_session.json).  In-memory state is cleared
# immediately on disconnect — this only gates the file-based recovery path.
# Activity staleness: how long since the last MCP POST before we consider the client
# disconnected.  Must cover normal idle gaps between tool calls — MCP Streamable HTTP
# is stateless with no heartbeat, so VS Code sends no POSTs when idle.
MCP_CAPABILITY_STALENESS_MINUTES: float = 30.0
MCP_ACTIVITY_STALENESS_SECONDS: float = 300.0  # 5 minutes


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Provider ---
    ANTHROPIC_API_KEY: str = Field(
        default="", description="Anthropic API key (starts with 'sk-'). Optional if using Claude CLI.",
    )

    # --- GitHub OAuth ---
    GITHUB_OAUTH_CLIENT_ID: str = Field(
        default="", description="GitHub OAuth app client ID for repo linking.",
    )
    GITHUB_OAUTH_CLIENT_SECRET: str = Field(
        default="", description="GitHub OAuth app client secret.",
    )

    # --- Security ---
    SECRET_KEY: str = Field(
        default="", description="Secret key for Fernet encryption. Auto-generated and persisted if not set.",
    )

    # --- Embedding ---
    EMBEDDING_MODEL: str = Field(
        default="all-MiniLM-L6-v2", description="Sentence-transformers model for 384-dim embeddings.",
    )

    # --- Rate Limiting ---
    OPTIMIZE_RATE_LIMIT: str = Field(
        default="10/minute", description="Rate limit for POST /api/optimize (limits library format).",
    )
    REFINE_RATE_LIMIT: str = Field(
        default="10/minute", description="Rate limit for POST /api/refine.",
    )
    FEEDBACK_RATE_LIMIT: str = Field(
        default="30/minute", description="Rate limit for POST /api/feedback.",
    )
    DEFAULT_RATE_LIMIT: str = Field(
        default="60/minute", description="Default rate limit for unlabeled endpoints.",
    )

    # --- Passthrough ---
    BIAS_CORRECTION_FACTOR: float = Field(
        default=0.85, description="Multiplicative bias correction for passthrough self-rated scores (0.0-1.0).",
    )

    # --- Context Budget ---
    MAX_CONTEXT_TOKENS: int = Field(
        default=80000, description="Maximum token budget for assembled optimization context.",
    )
    MAX_RAW_PROMPT_CHARS: int = Field(
        default=200000, description="Maximum allowed raw prompt length in characters.",
    )
    MAX_GUIDANCE_CHARS: int = Field(
        default=20000, description="Per-source character cap for agent guidance files.",
    )
    MAX_CODEBASE_CONTEXT_CHARS: int = Field(
        default=100000, description="Maximum codebase context characters injected into optimizer.",
    )
    MAX_ADAPTATION_CHARS: int = Field(
        default=5000, description="Maximum adaptation state characters injected into optimizer.",
    )
    EXPLORE_MAX_PROMPT_CHARS: int = Field(
        default=20000, description="Maximum prompt characters sent to explore synthesis.",
    )
    EXPLORE_MAX_CONTEXT_CHARS: int = Field(
        default=700000, description="Maximum raw codebase characters for explore retrieval.",
    )
    EXPLORE_MAX_FILES: int = Field(
        default=40, description="Maximum number of files retrieved during explore phase.",
    )
    EXPLORE_TOTAL_LINE_BUDGET: int = Field(
        default=15000, description="Total line budget across all retrieved files in explore.",
    )

    # --- Index Enrichment ---
    INDEX_OUTLINE_MAX_CHARS: int = Field(
        default=500, description="Maximum characters per file outline in RepoIndexService.",
    )
    INDEX_CURATED_MAX_CHARS: int = Field(
        default=8000, description="Maximum characters for curated codebase context in passthrough.",
    )
    INDEX_CURATED_MIN_SIMILARITY: float = Field(
        default=0.3, description="Minimum cosine similarity threshold for curated retrieval.",
    )
    INDEX_CURATED_MAX_PER_DIR: int = Field(
        default=3, description="Maximum files per directory in curated retrieval (diversity cap).",
    )
    INDEX_DOMAIN_BOOST: float = Field(
        default=1.3, description="Similarity multiplier for files matching the detected domain.",
    )

    # --- Network ---
    TRUSTED_PROXIES: str = Field(
        default="127.0.0.1", description="Comma-separated trusted proxy IPs for X-Forwarded-For.",
    )
    FRONTEND_URL: str = Field(
        default="http://localhost:5199", description="Frontend origin URL for CORS.",
    )
    DEVELOPMENT_MODE: bool = Field(
        default=False,
        description="Enable development mode (localhost CORS, relaxed cookie security).",
    )

    # --- MCP Auth ---
    MCP_AUTH_TOKEN: str | None = Field(
        default=None, description="Bearer token for MCP server auth. None = no auth (local dev).",
    )
    MCP_ALLOW_QUERY_TOKEN: bool = Field(
        default=True, description="Allow ?token= query param for SSE clients. Disable in production.",
    )

    # --- Explore Cache ---
    EXPLORE_RESULT_CACHE_TTL: int = Field(
        default=3600, description="Explore result cache TTL in seconds.",
    )

    # --- Models ---
    MODEL_SONNET: str = Field(
        default="claude-sonnet-4-6", description="Default Sonnet model ID for analyze/score phases.",
    )
    MODEL_OPUS: str = Field(
        default="claude-opus-4-6", description="Default Opus model ID for optimize phase.",
    )
    MODEL_HAIKU: str = Field(
        default="claude-haiku-4-5", description="Default Haiku model ID for suggest/explore/extract phases.",
    )

    # --- Traces ---
    TRACE_RETENTION_DAYS: int = Field(
        default=30, description="Number of days to retain JSONL trace files before cleanup.",
    )

    # --- Audit ---
    AUDIT_RETENTION_DAYS: int = Field(
        default=90, description="Days to retain audit log entries before auto-pruning.",
    )

    # --- Database ---
    DATABASE_URL: str = Field(
        default=f"sqlite+aiosqlite:///{DATA_DIR / 'synthesis.db'}",
        description="SQLAlchemy async database URL.",
    )

    def resolve_secret_key(self) -> str:
        """Auto-generate SECRET_KEY if not set, persist to data/.app_secrets."""
        if self.SECRET_KEY:
            return self.SECRET_KEY
        secrets_file = DATA_DIR / ".app_secrets"
        if secrets_file.exists():
            return secrets_file.read_text().strip()
        key = secrets.token_urlsafe(64)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        secrets_file.write_text(key)
        secrets_file.chmod(0o600)
        return key


settings = Settings()
