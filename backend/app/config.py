import logging

from pydantic import ConfigDict
from pydantic_settings import BaseSettings

_WEAK_DEFAULTS = {
    "SECRET_KEY": "synthesis-dev-secret-key",
    "JWT_SECRET": "dev-jwt-secret-change-in-prod",
    "JWT_REFRESH_SECRET": "dev-refresh-secret-change-in-prod",
}


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")
    ANTHROPIC_API_KEY: str = ""
    # GitHub App — user auth (OAuth flow uses App's client credentials)
    GITHUB_APP_CLIENT_ID: str = ""
    GITHUB_APP_CLIENT_SECRET: str = ""
    # GitHub App — bot / installation (for write operations)
    GITHUB_APP_ID: str = ""
    GITHUB_APP_PRIVATE_KEY: str = ""         # RSA private key PEM, \n-escaped
    GITHUB_APP_INSTALLATION_ID: str = ""
    SECRET_KEY: str = "synthesis-dev-secret-key"
    GITHUB_TOKEN_ENCRYPTION_KEY: str = ""
    FRONTEND_URL: str = "http://localhost:5199"
    MCP_PORT: int = 8001
    MCP_HOST: str = "127.0.0.1"
    MCP_PROBE_HOST: str = ""  # Docker: set to "mcp" (service name)
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/synthesis.db"
    CORS_ORIGINS: str = "http://localhost:5199,http://localhost:4173"

    # Per-stage LLM call timeout seconds.
    # Claude CLI provider spawns a new subprocess per call; cold-start alone
    # takes ~7–15 seconds on typical hardware. Timeouts are set generously
    # to survive startup + API latency. Override via env vars if needed.
    EXPLORE_TIMEOUT_SECONDS: int = 120   # Single-shot synthesis + retrieval (2 min)
    # Legacy agentic explore timeout — unused after semantic index migration
    # EXPLORE_AGENTIC_TIMEOUT_SECONDS: int = 600
    ANALYZE_TIMEOUT_SECONDS: int = 90    # Simple completion; CLI startup ~7–15s
    STRATEGY_TIMEOUT_SECONDS: int = 90   # Simple completion; same startup cost
    OPTIMIZE_TIMEOUT_SECONDS: int = 120  # Streaming rewrite; longest content
    VALIDATE_TIMEOUT_SECONDS: int = 90   # Simple completion; same startup cost

    # Maximum number of optimize+validate retry cycles on low score (default 1).
    # Set MAX_PIPELINE_RETRIES=2 (or higher) to enable a second retry that
    # tightens focus_areas to only the single lowest-scoring dimension.
    MAX_PIPELINE_RETRIES: int = 1

    PIPELINE_TIMEOUT_SECONDS: int = 900  # 15-minute outer limit

    # JWT authentication
    JWT_SECRET: str = "dev-jwt-secret-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_PRIVATE_KEY: str = ""
    JWT_PUBLIC_KEY: str = ""
    JWT_REFRESH_SECRET: str = "dev-refresh-secret-change-in-prod"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Set to True in production (behind HTTPS) to add Secure flag to cookies.
    JWT_COOKIE_SECURE: bool = False

    # Rate limiting — auth endpoints (limits library format: "N/period")
    RATE_LIMIT_AUTH_LOGIN: str = "20/minute"
    RATE_LIMIT_AUTH_CALLBACK: str = "10/minute"
    RATE_LIMIT_JWT_REFRESH: str = "60/minute"
    RATE_LIMIT_HISTORY: str = "60/minute"
    RATE_LIMIT_HISTORY_WRITE: str = "20/minute"
    RATE_LIMIT_OPTIMIZE: str = "10/minute"
    RATE_LIMIT_GITHUB_REPOS: str = "30/minute"
    RATE_LIMIT_GITHUB_REPOS_WRITE: str = "10/minute"
    RATE_LIMIT_SETTINGS: str = "30/minute"

    # Trusted reverse-proxy IPs (comma-separated). X-Forwarded-For is only
    # honoured when the direct connection comes from one of these addresses.
    # Defaults to loopback (127.0.0.1, ::1) when empty.
    TRUSTED_PROXIES: str = ""

    # Embedding / Repo Index
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    REPO_INDEX_TTL_HOURS: int = 24
    REPO_INDEX_MAX_FILES: int = 5000
    EXPLORE_INDEX_WAIT_TIMEOUT: int = 30     # seconds to wait for building index
    EXPLORE_FILE_READ_CONCURRENCY: int = 10  # parallel GitHub reads
    EXPLORE_MAX_FILES: int = 40              # max files to read for synthesis (up from 25)
    EXPLORE_TOTAL_LINE_BUDGET: int = 15_000  # total lines across all files for LLM context
    EXPLORE_MAX_LINES_PER_FILE: int = 500    # hard ceiling per file (dynamic budget may lower this)
    EXPLORE_MAX_AMBIGUOUS_MATCHES: int = 3   # skip prompt-referenced files with > N tree matches
    EXPLORE_MAX_CONTEXT_CHARS: int = 700_000  # ~175K tokens; char ceiling for LLM context payload
    EXPLORE_RESULT_CACHE_TTL: int = 3600     # 1 hour

    # Redis (optional — in-memory fallback when unavailable)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # Test mode — enables test-only endpoints. Never set True in production.
    TESTING: bool = False

    def model_post_init(self, __context) -> None:
        _log = logging.getLogger(__name__)
        for field, weak in _WEAK_DEFAULTS.items():
            if getattr(self, field) == weak:
                _log.warning(
                    "SECURITY: %s is using the default dev value — "
                    "set a strong random secret in .env before deploying.",
                    field,
                )
        # Production security check: warn when cookies are insecure outside localhost.
        _is_localhost = (
            self.FRONTEND_URL.startswith("http://localhost")
            or self.FRONTEND_URL.startswith("http://127.0.0.1")
        )
        # Block startup if production deployment uses weak default secrets.
        if not _is_localhost:
            weak_in_prod = [f for f, w in _WEAK_DEFAULTS.items() if getattr(self, f) == w]
            if weak_in_prod:
                raise RuntimeError(
                    f"FATAL: Production detected (FRONTEND_URL={self.FRONTEND_URL}) "
                    f"but these secrets use default dev values: {', '.join(weak_in_prod)}. "
                    f"Set strong random values in .env before deploying."
                )
        if not self.JWT_COOKIE_SECURE and not _is_localhost:
            _log.critical(
                "SECURITY: JWT_COOKIE_SECURE=False but FRONTEND_URL=%s is not localhost. "
                "Auth cookies will be sent over plaintext HTTP in production. "
                "Set JWT_COOKIE_SECURE=True in .env.",
                self.FRONTEND_URL,
            )


settings = Settings()
