from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")
    ANTHROPIC_API_KEY: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    SECRET_KEY: str = "promptforge-dev-secret-key"
    GITHUB_TOKEN_ENCRYPTION_KEY: str = ""
    MCP_PORT: int = 8001
    MCP_HOST: str = "127.0.0.1"
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/promptforge.db"
    CORS_ORIGINS: str = "http://localhost:5199,http://localhost:4173"

    # Per-stage LLM call timeout seconds.
    # Claude CLI provider spawns a new subprocess per call; cold-start alone
    # takes ~7–15 seconds on typical hardware. Timeouts are set generously
    # to survive startup + API latency. Override via env vars if needed.
    EXPLORE_TIMEOUT_SECONDS: int = 600   # Agentic multi-turn repo exploration (10 min)
    ANALYZE_TIMEOUT_SECONDS: int = 90    # Simple completion; CLI startup ~7–15s
    STRATEGY_TIMEOUT_SECONDS: int = 90   # Simple completion; same startup cost
    OPTIMIZE_TIMEOUT_SECONDS: int = 120  # Streaming rewrite; longest content
    VALIDATE_TIMEOUT_SECONDS: int = 90   # Simple completion; same startup cost

    # Maximum number of optimize+validate retry cycles on low score (default 1).
    # Set MAX_PIPELINE_RETRIES=2 (or higher) to enable a second retry that
    # tightens focus_areas to only the single lowest-scoring dimension.
    MAX_PIPELINE_RETRIES: int = 1

    PIPELINE_TIMEOUT_SECONDS: int = 900  # 15-minute outer limit


settings = Settings()
