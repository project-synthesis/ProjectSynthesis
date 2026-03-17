# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main` branch | ✅ Active |
| Older releases | ❌ No backports |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report suspected vulnerabilities privately so we can assess and patch before public disclosure.

### How to report

1. **GitHub Private Vulnerability Reporting** (preferred) — use the [Report a vulnerability](../../security/advisories/new) button on the Security tab of this repository.
2. **Email** — if the above is unavailable, email the maintainers at the address listed in the repository's GitHub profile. Encrypt your message with our PGP key if the disclosure is sensitive.

### What to include

- Description of the vulnerability and its potential impact
- Steps to reproduce (proof-of-concept or minimal repro case)
- Affected component (backend API, frontend, MCP server, GitHub OAuth flow, etc.)
- Any mitigations you are already aware of

### Response timeline

| Milestone | Target |
|-----------|--------|
| Acknowledgement | Within 48 hours |
| Initial assessment | Within 5 business days |
| Patch / mitigation | Within 30 days for critical; 90 days for moderate |
| Public disclosure | Coordinated with reporter after patch is available |

We follow [responsible disclosure](https://en.wikipedia.org/wiki/Responsible_disclosure). We will credit reporters in the release notes unless they prefer to remain anonymous.

## Security Design Notes

- **GitHub tokens** are Fernet-encrypted at rest in SQLite; the key is never logged
- **API keys** are Fernet-encrypted at rest in `data/.api_credentials`; only masked key (last 4 chars) returned by API
- **SECRET_KEY** auto-generated on first startup and persisted to `data/.app_secrets` (0o600 permissions)
- **MCP server** binds to `127.0.0.1` only by default; never expose it on a public interface
- **LLM output** is treated as untrusted text; it is not eval'd or executed
- **Strategy file paths** validated against path traversal via `.resolve()` + `.is_relative_to()` guard
- **Workspace roots content** wrapped in `<untrusted-context>` tags with per-file caps (500 lines / 10K chars)
