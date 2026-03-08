# Contributing to Project Synthesis

Thank you for considering contributing to Project Synthesis. This document explains how to get involved, what to expect, and how to make contributions that are easy to review and merge.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold it.

## Ways to Contribute

- **Bug reports** — open an issue using the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)
- **Feature requests** — open an issue using the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md)
- **Documentation improvements** — fix typos, clarify sections, add examples
- **Code contributions** — fix bugs, implement features, improve performance

## Development Setup

```bash
# 1. Fork and clone
git clone https://github.com/ProjectSynthesis/project-synthesis.git
cd project-synthesis

# 2. Start all services
./init.sh

# 3. Run backend tests
cd backend && source .venv/bin/activate && pytest

# 4. Run frontend type-check
cd frontend && npx tsc --noEmit
```

See the [CLAUDE.md](CLAUDE.md) file for full architecture and service details.

## Branch Naming

| Prefix | Use for |
|--------|---------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |
| `refactor/` | Code restructuring without behaviour change |
| `test/` | Tests only |
| `chore/` | Dependency bumps, CI config, tooling |

## Pull Request Process

1. **Open an issue first** for significant changes so we can discuss approach before you invest time coding.
2. **Keep PRs focused** — one logical change per PR. If you find a separate bug while working, open a separate PR.
3. **Write tests** — backend contributions should include pytest coverage; aim to keep coverage above 90%.
4. **Update documentation** — if your change affects the API, MCP tools, or configuration, update `docs/` and `CLAUDE.md`.
5. **Pass all checks** — `pytest`, `ruff`, `mypy`, and `tsc --noEmit` must all pass before requesting review.
6. **Sign-off** — all commits must be signed off as per the [Developer Certificate of Origin (DCO)](https://developercertificate.org/). Add `Signed-off-by: Your Name <email>` to each commit or use `git commit -s`.

## Code Style

- **Python**: [Black](https://black.readthedocs.io/) (line length 100) + [Ruff](https://docs.astral.sh/ruff/) + [Mypy](https://mypy.readthedocs.io/) strict
- **TypeScript/Svelte**: [Prettier](https://prettier.io/) defaults + ESLint
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/) format (`feat:`, `fix:`, `docs:`, etc.)

## Reporting Security Vulnerabilities

Do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
