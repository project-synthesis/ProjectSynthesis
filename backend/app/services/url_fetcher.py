"""Shared URL fetching service with HTML-to-markdown conversion.

Used by both the HTTP router and MCP server so content pre-processing
is identical regardless of the caller.

P2.4: strip_html now preserves document structure as markdown:
  - <h1>–<h4> → # / ## / ### / #### prefixes
  - <li>       → - bullet points
  - <code>/<pre> → fenced ``` code blocks
  - Everything else stripped to plain text as before
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# ── Remove noise blocks entirely ─────────────────────────────────────────────
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)

# ── Structural conversions (applied before generic tag stripping) ─────────────
# Headings — match opening tag, capture inner text (tags stripped later)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL | re.IGNORECASE)
_H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL | re.IGNORECASE)
_H4_RE = re.compile(r"<h4[^>]*>(.*?)</h4>", re.DOTALL | re.IGNORECASE)

# List items — capture inner content (may contain inline tags)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)

# Code/pre blocks — fenced markdown; strip inner tags but keep text verbatim
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_CODE_INLINE_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)

# Generic tag stripper (residual tags after structural conversion)
_TAG_RE = re.compile(r"<[^>]+>")

# Whitespace normaliser — collapse runs of spaces/tabs but preserve newlines
_HSPACE_RE = re.compile(r"[ \t]{2,}")
# Collapse more than two consecutive newlines to two
_EXCESS_NL_RE = re.compile(r"\n{3,}")


def _inner_text(match_text: str) -> str:
    """Strip any residual HTML tags from a captured group and normalise spaces."""
    return _HSPACE_RE.sub(" ", _TAG_RE.sub("", match_text)).strip()


def strip_html(html: str) -> str:
    """Convert HTML to structured markdown-ish text.

    Conversion order:
    1. Remove <script> and <style> blocks entirely.
    2. Convert headings to # / ## / ### / #### markdown prefixes.
    3. Convert <pre> blocks to fenced ``` code blocks.
    4. Convert inline <code> spans to fenced ``` code blocks.
    5. Convert <li> items to - bullet points.
    6. Strip all remaining tags.
    7. Normalise horizontal whitespace; collapse excess blank lines.
    """
    # 1. Remove noise
    text = _SCRIPT_RE.sub(" ", html)
    text = _STYLE_RE.sub(" ", text)

    # 2. Headings → markdown
    text = _H1_RE.sub(lambda m: f"\n# {_inner_text(m.group(1))}\n", text)
    text = _H2_RE.sub(lambda m: f"\n## {_inner_text(m.group(1))}\n", text)
    text = _H3_RE.sub(lambda m: f"\n### {_inner_text(m.group(1))}\n", text)
    text = _H4_RE.sub(lambda m: f"\n#### {_inner_text(m.group(1))}\n", text)

    # 3. <pre> → fenced code block (must come before generic tag strip)
    text = _PRE_RE.sub(lambda m: f"\n```\n{_inner_text(m.group(1))}\n```\n", text)

    # 4. Inline <code> → fenced code block (keeps snippets readable)
    text = _CODE_INLINE_RE.sub(lambda m: f"\n```\n{_inner_text(m.group(1))}\n```\n", text)

    # 5. <li> → bullet
    text = _LI_RE.sub(lambda m: f"\n- {_inner_text(m.group(1))}", text)

    # 6. Strip remaining tags
    text = _TAG_RE.sub(" ", text)

    # 7. Normalise whitespace
    text = _HSPACE_RE.sub(" ", text)
    text = _EXCESS_NL_RE.sub("\n\n", text)
    return text.strip()


async def fetch_url_contexts(url_contexts: list[str] | None) -> list[dict]:
    """Fetch and convert URL content to structured markdown; returns error entry on failure.

    Args:
        url_contexts: List of URLs to fetch.

    Returns:
        List of {"url": str, "content": str, "error": str | None} dicts, one per
        input URL (order preserved).  On success, ``error`` is ``None`` and
        ``content`` contains stripped markdown text capped at 3000 chars.  On any
        per-URL failure (network error, timeout, non-200 response) ``content`` is
        ``""`` and ``error`` holds the error message; the batch continues for the
        remaining URLs.
    """
    if not url_contexts:
        return []
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in url_contexts:
            try:
                resp = await client.get(url, headers={"User-Agent": "ProjectSynthesis/1.0"})
                resp.raise_for_status()
                content = strip_html(resp.text)[:3000]
                results.append({"url": url, "content": content, "error": None})
            except Exception as e:
                logger.warning("URL fetch failed for %s: %s", url, e)
                results.append({"url": url, "content": "", "error": str(e)})
    return results
