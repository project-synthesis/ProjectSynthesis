"""GitHub OAuth stubs — returns 501 until Phase 2."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/auth/login")
async def github_login():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")


@router.get("/auth/callback")
async def github_callback():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")


@router.get("/auth/me")
async def github_me():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")


@router.post("/auth/logout")
async def github_logout():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")
