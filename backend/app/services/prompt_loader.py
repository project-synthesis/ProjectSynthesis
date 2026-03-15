"""Template loading + variable substitution.

Templates are Markdown files with {{variable}} placeholders.
Variables with no value are omitted, including surrounding XML tags.
Templates are read from disk on each call (hot-reload, no restart needed).
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class PromptLoader:
    """Loads and renders prompt templates from the prompts directory."""

    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    @property
    def manifest(self) -> dict:
        """Load manifest.json (reloaded on each access for hot-reload)."""
        manifest_path = self.prompts_dir / "manifest.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text())
        return {}

    def load(self, name: str) -> str:
        """Load a template file as raw text (no substitution)."""
        path = self.prompts_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {path}")
        return path.read_text()

    def render(self, name: str, variables: dict[str, str | None] | None = None) -> str:
        """Load template and substitute variables.

        - Required variables (per manifest) must be present and non-empty.
        - Optional variables with None/empty value -> empty string.
        - Empty XML tag pairs are removed after substitution.
        """
        variables = variables or {}
        template = self.load(name)

        # Validate required variables
        spec = self.manifest.get(name, {})
        for required in spec.get("required", []):
            if not variables.get(required):
                raise ValueError(
                    f"Required variable '{required}' missing or empty for template '{name}'"
                )

        # Substitute variables
        for var_name, value in variables.items():
            placeholder = "{{" + var_name + "}}"
            template = template.replace(placeholder, value or "")

        # Remove any remaining unsubstituted optional placeholders
        template = re.sub(r"\{\{[a-z_]+\}\}", "", template)

        # Remove empty XML tags (tags with only whitespace content)
        template = re.sub(r"<([\w-]+)>\s*</\1>", "", template, flags=re.DOTALL)

        # Clean up excessive blank lines
        template = re.sub(r"\n{3,}", "\n\n", template)

        return template.strip()
