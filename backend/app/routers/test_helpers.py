# backend/app/routers/test_helpers.py
"""Test-only endpoints. Only mounted when TESTING=True. Never imported in production."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.auth import User
from app.utils.jwt import sign_access_token

router = APIRouter(tags=["test-helpers"])


class TestTokenRequest(BaseModel):
    email: str = "e2e@test.com"
    github_login: str = "e2e-user"
    is_new_user: bool = False


@router.post("/test/token")
async def issue_test_token(
    body: TestTokenRequest,
    session: AsyncSession = Depends(get_session),
):
    """Issue a pre-signed JWT for E2E tests. Never available in production."""
    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            id=str(uuid.uuid4()),
            github_user_id=abs(hash(body.email)) % (10**9),
            github_login=body.github_login,
            email=body.email,
            display_name=body.github_login,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = sign_access_token(
        user_id=user.id,
        github_login=body.github_login,
        roles=["user"],
    )
    return {"access_token": token, "user_id": user.id, "is_new_user": body.is_new_user}
