from typing import Optional

from pydantic import BaseModel, Field


class RepoInfo(BaseModel):
    full_name: str
    name: str
    private: bool = False
    default_branch: str = "main"
    description: Optional[str] = None
    language: Optional[str] = None
    size_kb: int = 0
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    updated_at: Optional[str] = None    # ISO-8601 string
    pushed_at: Optional[str] = None     # ISO-8601 string
    license_name: Optional[str] = None  # SPDX ID e.g. "MIT", "Apache-2.0"
    topics: list[str] = []


class LinkRepoRequest(BaseModel):
    full_name: str = Field(..., description="Repository full name (owner/repo)")
    branch: Optional[str] = Field(None, description="Branch name, defaults to repo default")


class LinkedRepoResponse(BaseModel):
    id: str
    full_name: str
    branch: str
    default_branch: Optional[str] = None
    language: Optional[str] = None
    linked_at: Optional[str] = None


class GitHubUserInfo(BaseModel):
    connected: bool = False
    login: Optional[str] = None
    avatar_url: Optional[str] = None
    github_user_id: Optional[int] = None
    token_type: Optional[str] = None
