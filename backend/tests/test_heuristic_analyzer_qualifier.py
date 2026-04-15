"""Tests for domain qualifier enrichment in the heuristic analyzer."""

from __future__ import annotations


def test_enrich_backend_auth():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "backend",
        "implement jwt authentication middleware with session token refresh",
    )
    assert result == "backend: auth"


def test_enrich_backend_api():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "backend",
        "design a rest api endpoint with graphql fallback handler",
    )
    assert result == "backend: api"


def test_enrich_no_match_returns_original():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "backend",
        "refactor the logging system for better readability",
    )
    assert result == "backend"


def test_enrich_requires_min_keyword_hits():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    # "token" hits auth (1 hit) — below threshold of 2
    result = _enrich_domain_qualifier(
        "backend",
        "clean up the token validation logic",
    )
    assert result == "backend"


def test_enrich_unknown_domain_returns_original():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "marketing",
        "implement jwt authentication middleware",
    )
    assert result == "marketing"


def test_enrich_already_qualified_returns_as_is():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "backend: security",
        "implement jwt authentication",
    )
    assert result == "backend: security"


def test_enrich_frontend_components():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "frontend",
        "create a reusable component with proper render lifecycle and layout grid",
    )
    assert result == "frontend: components"


def test_enrich_devops_infra():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    result = _enrich_domain_qualifier(
        "devops",
        "set up docker container orchestration with kubernetes deployment",
    )
    assert result == "devops: infra"


def test_enrich_picks_top_qualifier_by_hits():
    from app.services.heuristic_analyzer import _enrich_domain_qualifier
    # auth: oauth(1) + session(1) + authentication(1) + jwt(1) + token(1) = 5
    # api: endpoint(1) = 1
    result = _enrich_domain_qualifier(
        "backend",
        "implement oauth session authentication with jwt token endpoint",
    )
    assert result == "backend: auth"
