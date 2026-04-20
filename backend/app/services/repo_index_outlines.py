"""Structured outline extraction for the repo file index.

Split from ``repo_index_service.py`` (2026-04-19): these pure functions
run during indexing to produce the ``FileOutline`` used as both the
embedding text seed and the human-readable summary stored in
``RepoFileIndex.outline``. They take ``(path, content, lines)`` and
return a ``FileOutline`` without touching the DB or network — keeping
them separate makes it cheap to unit-test and reduces the surface area
of the indexing service.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Callable

from app.config import settings

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class FileOutline:
    file_path: str
    file_type: str
    structural_summary: str
    imports_summary: str | None = None
    doc_summary: str | None = None
    size_lines: int = 0
    size_bytes: int = 0


# ---------------------------------------------------------------------------
# Per-language extractors
# ---------------------------------------------------------------------------

def _extract_python_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    doc = _extract_docstring(lines)
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*(class |(?:async )?def )\w+", ln)
    ][:15]
    # Condensed imports summary
    imports = [
        re.sub(r"\s+", " ", ln.strip())
        for ln in lines
        if re.match(r"^(import |from \S+ import )", ln)
    ]
    imports_summary = "imports: " + ", ".join(
        re.sub(r"^(?:from \S+ )?import ", "", i) for i in imports[:20]
    ) if imports else None
    # __all__ exports
    all_match = re.search(r"^__all__\s*=\s*\[([^\]]+)\]", content, re.MULTILINE)
    if all_match:
        exports = all_match.group(1).replace('"', "").replace("'", "").strip()
        imports_summary = (imports_summary + f" | __all__: [{exports}]") if imports_summary else f"__all__: [{exports}]"
    return FileOutline(
        file_path=path, file_type="python",
        structural_summary="\n".join(sigs),
        imports_summary=imports_summary,
        doc_summary=doc,
    )


def _extract_typescript_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    doc = None
    if content.startswith("/**"):
        end = content.find("*/")
        if end != -1:
            doc = content[3:end].strip().split("\n")[0].strip(" *")
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(
            r"^export\s+(interface|type|function|async function|class|const)\s+\w+",
            ln,
        )
    ][:15]
    return FileOutline(
        file_path=path, file_type="typescript",
        structural_summary="\n".join(sigs),
        doc_summary=doc,
    )


def _extract_markdown_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    headings = [
        ln.rstrip() for ln in lines if re.match(r"^#{1,2}\s+", ln)
    ][:10]
    first_para = ""
    for ln in lines:
        if ln.strip() and not ln.startswith("#"):
            first_para = ln.strip()
            break
    summary = "\n".join(headings)
    return FileOutline(
        file_path=path, file_type="docs",
        structural_summary=summary,
        doc_summary=first_para[:200] if first_para else None,
    )


def _extract_config_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    preview = "\n".join(lines[:15])
    return FileOutline(
        file_path=path, file_type="config",
        structural_summary=preview,
    )


def _extract_sql_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    stmts = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(CREATE\s+(TABLE|INDEX|FUNCTION|VIEW))", ln, re.IGNORECASE)
    ][:15]
    return FileOutline(
        file_path=path, file_type="sql",
        structural_summary="\n".join(stmts),
    )


def _extract_svelte_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    exports = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*export\s+(let|const|function)\s+", ln)
    ][:10]
    # Svelte 5 runes ($props, $state, $derived)
    runes = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^\s*let\s+.*\$(?:props|state|derived)\(", ln)
    ][:10]
    component_name = path.rsplit("/", 1)[-1].replace(".svelte", "")
    all_sigs = exports + runes
    summary = f"Component: {component_name}\n" + "\n".join(all_sigs)
    return FileOutline(
        file_path=path, file_type="svelte",
        structural_summary=summary,
    )


def _extract_generic_outline(path: str, content: str, lines: list[str]) -> FileOutline:
    sigs = [
        ln.rstrip()
        for ln in lines
        if re.match(r"^(class |def |function |export )", ln)
    ][:15]
    if not sigs:
        non_empty = [ln for ln in lines if ln.strip()][:20]
        sigs = non_empty
    return FileOutline(
        file_path=path, file_type="other",
        structural_summary="\n".join(sigs),
    )


def _extract_docstring(lines: list[str]) -> str | None:
    """Extract first paragraph of a Python module docstring."""
    in_doc = False
    doc_lines: list[str] = []
    for ln in lines[:30]:
        stripped = ln.strip()
        if not in_doc and stripped.startswith('"""'):
            in_doc = True
            content = stripped[3:]
            if content.endswith('"""') and len(content) > 3:
                return content[:-3].strip()
            if content:
                doc_lines.append(content)
            continue
        if in_doc:
            if '"""' in stripped:
                before = stripped.split('"""')[0].strip()
                if before:
                    doc_lines.append(before)
                break
            if stripped == "" and doc_lines:
                break  # First paragraph only
            doc_lines.append(stripped)
    return " ".join(doc_lines).strip() or None


_OUTLINE_EXTRACTORS: dict[str, Callable[[str, str, list[str]], FileOutline]] = {
    ".py": _extract_python_outline,
    ".ts": _extract_typescript_outline,
    ".js": _extract_typescript_outline,
    ".tsx": _extract_typescript_outline,
    ".jsx": _extract_typescript_outline,
    ".svelte": _extract_svelte_outline,
    ".json": _extract_config_outline,
    ".yaml": _extract_config_outline,
    ".yml": _extract_config_outline,
    ".toml": _extract_config_outline,
    ".md": _extract_markdown_outline,
    ".sql": _extract_sql_outline,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_structured_outline(path: str, content: str) -> FileOutline:
    """Extract a structured outline from file content based on file type."""
    ext = path[path.rfind("."):].lower() if "." in path else ""
    lines = content.splitlines()
    size_lines = len(lines)
    size_bytes = len(content.encode("utf-8", errors="replace"))
    max_chars = settings.INDEX_OUTLINE_MAX_CHARS

    extractor = _OUTLINE_EXTRACTORS.get(ext, _extract_generic_outline)
    outline = extractor(path, content, lines)
    outline.size_lines = size_lines
    outline.size_bytes = size_bytes

    # Enforce max chars
    if len(outline.structural_summary) > max_chars:
        outline.structural_summary = outline.structural_summary[:max_chars]
    return outline


def build_embedding_text(path: str, outline: FileOutline) -> str:
    """Combine path + structural info for richer embedding."""
    parts = [path]
    if outline.doc_summary:
        parts.append(outline.doc_summary)
    if outline.structural_summary:
        parts.append(outline.structural_summary)
    return " | ".join(parts)[:1000]


def build_content_sha(
    path: str, outline: FileOutline, model: str | None = None,
) -> str:
    """Return a stable dedup key for the embedding of ``path`` + ``outline``.

    The hash input includes the embedding-model identifier so that a
    model upgrade automatically invalidates every persisted vector: an
    old row's ``content_sha`` won't match the new formula, so the dedup
    query misses, we re-embed under the new model, and the refreshed
    row gets a fresh SHA. Without this fence a model change would
    silently reuse stale vectors forever.
    """
    model_id = model if model is not None else settings.EMBEDDING_MODEL
    embed_text = build_embedding_text(path, outline)
    return hashlib.sha256(
        f"{model_id}|{embed_text}".encode("utf-8"),
    ).hexdigest()


# Private-name aliases — preserved because the test module imports under
# these names and other internal call sites use them.
_extract_structured_outline = extract_structured_outline
_build_embedding_text = build_embedding_text
_build_content_sha = build_content_sha


__all__ = [
    "FileOutline",
    "extract_structured_outline",
    "build_embedding_text",
    "build_content_sha",
    # Private-name aliases retained for backward compatibility
    "_extract_structured_outline",
    "_build_embedding_text",
    "_build_content_sha",
]
