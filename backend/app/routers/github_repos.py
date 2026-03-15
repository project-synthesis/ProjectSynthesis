"""GitHub repos stubs — returns 501 until Phase 2."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/repos")
async def list_repos():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")


@router.post("/repos/link")
async def link_repo():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")


@router.get("/repos/linked")
async def get_linked():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")


@router.delete("/repos/unlink")
async def unlink_repo():
    raise HTTPException(status_code=501, detail="GitHub integration not yet implemented")
