# backend/tests/integration/conftest.py
"""Shared fixtures for integration tests.

Each test MODULE gets its own SQLite file (scope="module") so files are isolated
but tests within a file share one DB — fast and clean.

Provider is injected via app.state (not Depends) so we set it directly.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Force TESTING mode before importing the app so test_helpers router is mounted
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("SECRET_KEY", "integration-test-secret-key-32chars!!")
os.environ.setdefault("GITHUB_TOKEN_ENCRYPTION_KEY", "Zm9vYmFyYmF6cXV4cXV4cXV4cXV4cXV4cXV4cXU=")

from app.database import Base, get_session  # noqa: E402
from app.main import app, asgi_app  # noqa: E402
from app.models.auth import User  # noqa: E402
from app.providers.mock import MockProvider  # noqa: E402
from app.utils.jwt import sign_access_token  # noqa: E402

# ── Test user constants ────────────────────────────────────────────────────

TEST_USER_ID = "test-user-00000000-0000-0000-0000-000000000001"
TEST_USER_EMAIL = "test@integration.test"
TEST_USER_LOGIN = "test-user"

OTHER_USER_ID = "other-user-00000000-0000-0000-0000-000000000002"
OTHER_USER_LOGIN = "other-user"


# ── DB fixtures ───────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def engine(tmp_path_factory: pytest.TempPathFactory):
    db_path = tmp_path_factory.mktemp("integration_db") / "test.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _make_session_override(eng):
    """Return a FastAPI dependency override that yields sessions from *eng*.

    Mirrors the real get_session: commits on success, rolls back on error.
    This ensures PATCH/DELETE endpoints that rely on get_session's auto-commit
    actually persist their changes during tests.
    """
    TestSession = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    async def override() -> AsyncGenerator[AsyncSession, None]:
        async with TestSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return override


# ── App client fixture ────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def client(engine) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_session] = _make_session_override(engine)
    app.state.provider = MockProvider()
    async with AsyncClient(
        transport=ASGITransport(app=asgi_app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()
    app.state.provider = None


# ── Seeded user fixtures ───────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def seeded_users(engine):
    """Insert the two test users once per module."""
    from sqlalchemy import select
    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with TestSession() as session:
        for uid, login, email, gh_id in [
            (TEST_USER_ID,   TEST_USER_LOGIN,  TEST_USER_EMAIL,  10001),
            (OTHER_USER_ID,  OTHER_USER_LOGIN, "other@test.test", 10002),
        ]:
            exists = (await session.execute(
                select(User).where(User.id == uid)
            )).scalar_one_or_none()
            if not exists:
                session.add(User(
                    id=uid,
                    github_user_id=gh_id,
                    github_login=login,
                    email=email,
                    display_name=login,
                ))
        await session.commit()


# ── Auth header fixtures ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def auth_headers(seeded_users) -> dict[str, str]:
    token = sign_access_token(
        user_id=TEST_USER_ID,
        github_login=TEST_USER_LOGIN,
        roles=["user"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def other_auth_headers(seeded_users) -> dict[str, str]:
    token = sign_access_token(
        user_id=OTHER_USER_ID,
        github_login=OTHER_USER_LOGIN,
        roles=["user"],
    )
    return {"Authorization": f"Bearer {token}"}
