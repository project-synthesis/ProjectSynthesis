"""Async wrappers for PyGithub operations.

PyGithub is synchronous. All blocking calls are wrapped with
anyio.to_thread.run_sync() for use in async FastAPI endpoints.
"""

import logging

import anyio

logger = logging.getLogger(__name__)


async def _get_decrypted_token(session_id: str) -> str:
    """Retrieve the GitHub token for a session, triggering auto-refresh if needed.

    Routes through get_token_for_session so GitHub App user tokens are
    transparently refreshed when close to expiry — preserving the refresh
    invariant for the codebase-explorer (Stage 0) code path.
    """
    from app.database import async_session
    from app.services.github_service import get_token_for_session

    async with async_session() as session:
        token = await get_token_for_session(session, session_id)
        if not token:
            raise ValueError(f"No GitHub token found for session {session_id}")
        return token


async def validate_repo_access(
    token: str,
    full_name: str,
) -> dict:
    """Validate that a token has access to a repository.

    Returns repo metadata dict or raises ValueError.
    """
    def _sync():
        from github import Auth, Github
        g = Github(auth=Auth.Token(token))
        repo = g.get_repo(full_name)
        return {
            "full_name": repo.full_name,
            "default_branch": repo.default_branch,
            "language": repo.language,
            "private": repo.private,
        }

    return await anyio.to_thread.run_sync(_sync)
