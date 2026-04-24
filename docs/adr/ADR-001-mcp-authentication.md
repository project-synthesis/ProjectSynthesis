# ADR-001: MCP Server Authentication Strategy

**Status:** Accepted
**Date:** 2026-03-25

## Context

The MCP server on port 8001 exposes 14 tools (as of v0.4.2 — `synthesis_delete` added alongside the bulk-delete surface) for prompt optimization, history, taxonomy, and feedback. Currently unauthenticated — acceptable for local dev but a risk if exposed to untrusted networks. The MCP ecosystem is evolving toward remote Streamable HTTP transport, enabling cloud-hosted IDE plugins (Notion, Figma, etc.) to connect over the network.

## Decision

Environment-gated bearer token authentication via ASGI middleware:

- `MCP_AUTH_TOKEN` not set → no auth enforced (local dev, zero friction)
- `MCP_AUTH_TOKEN` set → `Authorization: Bearer <token>` required on all requests
- SSE fallback: `?token=<value>` accepted when `MCP_ALLOW_QUERY_TOKEN=True` (disable in production — tokens in query strings appear in logs)
- Nginx proxy guard as defense-in-depth layer
- Token comparison uses `hmac.compare_digest()` to prevent timing attacks

## Alternatives Considered

1. **Session-forwarding** — piggyback on GitHub OAuth session. Rejected: breaks headless IDE clients that don't have a browser session.
2. **Localhost-only binding** — bind MCP to 127.0.0.1 only. Rejected: prevents future remote integrations (Notion, Figma, multi-machine workflows).
3. **OAuth-based MCP auth** — full OAuth flow for MCP clients. Deferred: heavier implementation, better as a dedicated feature pass when remote MCP becomes common.

## Consequences

- Zero friction for local development (default behavior unchanged)
- Single env var enables full auth for production/remote deployments
- Any MCP client can pass the token via standard HTTP headers
- Future upgrade path to OAuth without breaking changes
- Query param fallback creates a log-hygiene concern — document nginx log masking

## Implementation status

**Shipped.** Middleware and env gates are in production.

- `MCP_AUTH_TOKEN` + `MCP_ALLOW_QUERY_TOKEN` defined in `backend/app/config.py:158-161`
- Bearer-token middleware wired at `backend/app/mcp_server.py:528` (no-op when `MCP_AUTH_TOKEN` is `None`) and registered at `backend/app/mcp_server.py:937-938`
- Regression tests in `backend/tests/test_security_hardening.py`

Default deployment leaves `MCP_AUTH_TOKEN` unset (zero-friction local dev, matches the decision). Production/remote deployments opt in by setting the env var — no code change required.
