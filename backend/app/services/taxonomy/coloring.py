"""OKLab color generation for taxonomy nodes.

Assigns perceptually distinct colors to taxonomy nodes based on their
UMAP 3D position. Uses OKLab color space for perceptual uniformity.

Spec Section 8.6.
"""

# ruff: noqa: N803, N806, E741 — mathematical notation (L, dL, l/m/s for LMS)

from __future__ import annotations

import logging
import math
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core OKLab ↔ sRGB conversion
# ---------------------------------------------------------------------------

def _linear_to_srgb(c: float) -> float:
    """Apply sRGB gamma encoding to a linear component."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _srgb_to_linear(c: float) -> float:
    """Remove sRGB gamma encoding from a gamma-encoded component."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def oklab_to_hex(L: float, a: float, b: float) -> str:
    """Convert OKLab coordinates to sRGB hex string.

    Args:
        L: Lightness [0, 1].
        a: Green–red axis (negative = green, positive = red). Typical ±0.4.
        b: Blue–yellow axis (negative = blue, positive = yellow). Typical ±0.4.

    Returns:
        Hex color string like ``#rrggbb``.  Out-of-gamut values are clamped.
    """
    # OKLab → LMS (cube-root basis)
    l = L + 0.3963377774 * a + 0.2158037573 * b
    m = L - 0.1055613458 * a - 0.0638541728 * b
    s = L - 0.0894841775 * a - 1.2914855480 * b

    # Cube to get linear LMS
    l3, m3, s3 = l ** 3, m ** 3, s ** 3

    # LMS → linear sRGB
    r_lin = +4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g_lin = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    b_lin = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3

    # Gamma-encode and clamp to [0, 255]
    def _to_byte(c_lin: float) -> int:
        c_gamma = _linear_to_srgb(max(0.0, min(1.0, c_lin)))
        return max(0, min(255, round(c_gamma * 255)))

    r8 = _to_byte(r_lin)
    g8 = _to_byte(g_lin)
    b8 = _to_byte(b_lin)

    return f"#{r8:02x}{g8:02x}{b8:02x}"


def hex_to_oklab(hex_color: str) -> tuple[float, float, float]:
    """Convert sRGB hex string to OKLab coordinates.

    Args:
        hex_color: Hex string like ``#rrggbb`` (case-insensitive).

    Returns:
        ``(L, a, b)`` tuple in OKLab space.
    """
    h = hex_color.lstrip("#")
    r8 = int(h[0:2], 16)
    g8 = int(h[2:4], 16)
    b8 = int(h[4:6], 16)

    # sRGB → linear
    r_lin = _srgb_to_linear(r8 / 255.0)
    g_lin = _srgb_to_linear(g8 / 255.0)
    b_lin = _srgb_to_linear(b8 / 255.0)

    # linear sRGB → LMS (inverse of the above matrix, standard OKLab)
    l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
    m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
    s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

    # Cube-root
    l_ = l ** (1.0 / 3.0) if l >= 0 else -((-l) ** (1.0 / 3.0))
    m_ = m ** (1.0 / 3.0) if m >= 0 else -((-m) ** (1.0 / 3.0))
    s_ = s ** (1.0 / 3.0) if s >= 0 else -((-s) ** (1.0 / 3.0))

    # LMS cube-root → OKLab
    L_out = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a_out = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_out = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

    return L_out, a_out, b_out


def derive_sub_domain_color(parent_hex: str) -> str:
    """Derive a sub-domain color from its parent domain's base color.

    Preserves hue (a/b channels) and shifts lightness down by 0.12 for
    visual subordination. The result is a perceptually darker variant of
    the same color family.
    """
    L, a, b = hex_to_oklab(parent_hex)
    L_sub = max(0.40, min(0.85, L - 0.12))
    return oklab_to_hex(L_sub, a, b)


# ---------------------------------------------------------------------------
# Color generation from UMAP position
# ---------------------------------------------------------------------------

def generate_color(umap_x: float, umap_y: float, umap_z: float) -> str:
    """Generate a perceptually uniform color from a UMAP 3D position.

    - Lightness is fixed at L=0.72 for readability on dark (#06060c) backgrounds.
    - Hue is derived from ``atan2(umap_y, umap_x)``.
    - Chroma is fixed at 0.20 (OKLab extended gamut), modulated slightly by
      ``umap_z`` in the range ±0.05.

    Args:
        umap_x: X coordinate of the UMAP embedding.
        umap_y: Y coordinate of the UMAP embedding.
        umap_z: Z coordinate of the UMAP embedding (modulates chroma).

    Returns:
        Hex color string like ``#rrggbb``.
    """
    L = 0.72
    base_chroma = 0.20

    # Modulate chroma by z: ±0.05 variation clamped to [0.10, 0.25]
    z_norm = math.tanh(umap_z)  # squash to (-1, 1)
    chroma = max(0.10, min(0.25, base_chroma + 0.05 * z_norm))

    # Hue angle from x/y plane
    hue = math.atan2(umap_y, umap_x)

    a = chroma * math.cos(hue)
    b = chroma * math.sin(hue)

    return oklab_to_hex(L, a, b)


# ---------------------------------------------------------------------------
# Perceptual distance and sibling enforcement
# ---------------------------------------------------------------------------

def delta_e_oklab(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """Euclidean distance in OKLab space (approximates ΔE).

    Args:
        lab1: ``(L, a, b)`` tuple for the first color.
        lab2: ``(L, a, b)`` tuple for the second color.

    Returns:
        Scalar distance value.
    """
    dL = lab1[0] - lab2[0]
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]
    return math.sqrt(dL * dL + da * da + db * db)


def enforce_minimum_delta_e(
    colors: Sequence[tuple[str, str]],
    min_delta_e: float = 0.04,
) -> list[tuple[str, str]]:
    """Ensure all sibling colors differ by at least ``min_delta_e`` in OKLab.

    When two colors are too close, the second one's hue is rotated by small
    increments until sufficient separation is achieved.

    Args:
        colors: List of ``(id, hex_color)`` pairs.
        min_delta_e: Minimum OKLab Euclidean distance between any two colors.

    Returns:
        New list of ``(id, hex_color)`` pairs with enforced separation.
        The first color in each conflicting pair is never modified.
    """
    if not colors:
        return []

    result: list[tuple[str, str]] = [colors[0]]

    for i in range(1, len(colors)):
        node_id, hex_color = colors[i]
        L, a, b = hex_to_oklab(hex_color)
        chroma = math.sqrt(a * a + b * b)
        hue = math.atan2(b, a)

        # Try rotating hue until we achieve min separation from all prior colors
        step = 0.15  # radians (~8.6°), small enough to be subtle
        for attempt in range(24):  # up to ~2π of rotation
            lab_candidate = (L, chroma * math.cos(hue), chroma * math.sin(hue))
            too_close = any(
                delta_e_oklab(lab_candidate, hex_to_oklab(prev_hex)) < min_delta_e
                for _, prev_hex in result
            )
            if not too_close:
                break
            hue += step
        else:
            # Fallback: use last attempted hue anyway
            logger.debug(
                "Hue rotation exhausted for node %s after 24 attempts", node_id,
            )

        new_hex = oklab_to_hex(L, chroma * math.cos(hue), chroma * math.sin(hue))
        result.append((node_id, new_hex))

    return result


# ---------------------------------------------------------------------------
# Seed palette — brand-anchored colors for canonical domain labels
# ---------------------------------------------------------------------------
#
# Mirrors the hex values in
# ``alembic/versions/a1b2c3d4e5f6_add_domain_nodes.py`` (SEED_DOMAINS).
# Keep the two in sync: the migration stamps these on first boot; this
# dict lets the taxonomy engine *re-stamp* them when a dissolved seed
# label emerges again through organic domain promotion.  Without this
# memory, a re-promoted "backend" would be assigned whatever OKLab cell
# the max-distance search produces — usually visually plausible but
# brand-inconsistent across dissolution/re-promotion cycles.
#
# Rationale: seed domains are not special at dissolution time (ADR-006
# — no permanence beyond "general"), but their *palette identity* IS
# special.  Users build mental models around "backend is purple, frontend
# is pink".  Breaking that across cycles is worse than picking a fresh
# color for a genuinely new label.
SEED_PALETTE: dict[str, str] = {
    "backend": "#b44aff",
    "frontend": "#ff4895",
    "database": "#36b5ff",
    "devops": "#6366f1",
    "security": "#ff2255",
    "fullstack": "#d946ef",
    "data": "#b49982",
    "general": "#7a7a9e",
}


def resolve_seed_palette_color(label: str) -> str | None:
    """Return the seed-palette hex for ``label`` if one exists.

    Lookup is case-insensitive and trims whitespace.  Returns ``None``
    for any label not in the canonical seed set — callers should fall
    back to :func:`compute_max_distance_color` in that case so novel
    domains still get OKLab-distributed hues.
    """
    if not label:
        return None
    return SEED_PALETTE.get(label.strip().lower())


# ---------------------------------------------------------------------------
# Domain color pinning — max-distance selection
# ---------------------------------------------------------------------------

def compute_max_distance_color(existing_hex: list[str]) -> str:
    """Find the OKLab color maximally distant from all existing domain colors.

    Also avoids tier accent colors to prevent perceptual confusion with the
    tier badges (internal=#00e5ff, sampling=#22ff88, passthrough=#fbbf24).

    The search is performed over a 50×50 grid in the (a, b) plane of OKLab
    space with fixed lightness L=0.7 for neon readability on dark backgrounds.

    Args:
        existing_hex: List of hex color strings already in use (may be empty).

    Returns:
        Hex color string like ``#rrggbb``.
    """
    try:
        from app.services.pipeline_constants import BRAND_RESERVED_COLORS

        all_hex = [h for h in existing_hex + BRAND_RESERVED_COLORS if h and h.startswith("#")]
        if not all_hex:
            return "#a855f7"  # Default purple when no reference colors exist

        existing_lab = [hex_to_oklab(h) for h in all_hex]

        best_color: tuple[float, float, float] | None = None
        best_min_dist = 0.0

        # Sample candidates in OKLab (a, b) plane; L=0.7 gives neon brightness
        for a_val in np.linspace(-0.15, 0.15, 50):
            for b_val in np.linspace(-0.15, 0.15, 50):
                candidate = (0.7, float(a_val), float(b_val))
                min_dist = min(
                    delta_e_oklab(candidate, e) for e in existing_lab
                )
                if min_dist > best_min_dist:
                    best_min_dist = min_dist
                    best_color = candidate

        if best_color is None:
            return "#7a7a9e"

        return oklab_to_hex(best_color[0], best_color[1], best_color[2])
    except Exception:
        logger.warning("OKLab color computation failed, using fallback gray", exc_info=True)
        return "#7a7a9e"
