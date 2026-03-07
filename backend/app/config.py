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

    # Per-stage LLM call timeout seconds (spec latency target + safety buffer).
    # Override via env vars to tune for slow providers.
    EXPLORE_TIMEOUT_SECONDS: int = 90    # 30s spec target + 60s buffer
    ANALYZE_TIMEOUT_SECONDS: int = 10    # 5s spec target + 5s buffer
    STRATEGY_TIMEOUT_SECONDS: int = 20   # 10s spec target + 10s buffer
    OPTIMIZE_TIMEOUT_SECONDS: int = 40   # 20s spec target + 20s buffer
    VALIDATE_TIMEOUT_SECONDS: int = 10   # 5s spec target + 5s buffer


settings = Settings()
