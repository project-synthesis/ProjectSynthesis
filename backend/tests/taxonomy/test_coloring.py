"""Tests for OKLab color generation."""

import re

from app.services.taxonomy.coloring import (
    SEED_PALETTE,
    enforce_minimum_delta_e,
    generate_color,
    oklab_to_hex,
    resolve_seed_palette_color,
)


def _is_hex(s: str) -> bool:
    return bool(re.match(r"^#[0-9a-fA-F]{6}$", s))


class TestSeedPalette:
    """Brand-anchored seed colors survive dissolution/re-promotion cycles."""

    def test_palette_covers_alembic_seeds(self):
        """SEED_PALETTE must mirror the alembic migration's SEED_DOMAINS."""
        expected = {
            "backend", "frontend", "database", "devops",
            "security", "fullstack", "data", "general",
        }
        assert set(SEED_PALETTE.keys()) == expected
        # Every seed value must be a valid hex color
        for label, hex_color in SEED_PALETTE.items():
            assert _is_hex(hex_color), f"{label} has invalid hex: {hex_color}"

    def test_resolve_canonical_labels(self):
        assert resolve_seed_palette_color("backend") == "#b44aff"
        assert resolve_seed_palette_color("frontend") == "#ff4895"
        assert resolve_seed_palette_color("general") == "#7a7a9e"

    def test_resolve_case_insensitive(self):
        assert resolve_seed_palette_color("Backend") == "#b44aff"
        assert resolve_seed_palette_color("FRONTEND") == "#ff4895"
        assert resolve_seed_palette_color("  frontend  ") == "#ff4895"

    def test_resolve_unknown_label_returns_none(self):
        assert resolve_seed_palette_color("marketing") is None
        assert resolve_seed_palette_color("saas-pricing") is None

    def test_resolve_empty_label_returns_none(self):
        assert resolve_seed_palette_color("") is None
        assert resolve_seed_palette_color(None) is None  # type: ignore[arg-type]

    def test_palette_colors_are_pairwise_distinct(self):
        """Brand compliance: no two seed labels share a color."""
        hex_values = list(SEED_PALETTE.values())
        assert len(set(hex_values)) == len(hex_values)


class TestGenerateColor:
    def test_returns_valid_hex(self):
        color = generate_color(0.0, 0.0, 0.0)
        assert _is_hex(color)

    def test_different_positions_different_colors(self):
        c1 = generate_color(0.5, 0.5, 0.5)
        c2 = generate_color(-0.5, -0.5, 0.5)
        assert c1 != c2

    def test_deterministic(self):
        c1 = generate_color(0.3, -0.7, 0.1)
        c2 = generate_color(0.3, -0.7, 0.1)
        assert c1 == c2

    def test_dark_background_readable(self):
        """L=0.72 should give bright-enough colors for #06060c background."""
        color = generate_color(0.0, 0.0, 0.5)
        # Convert hex to approximate luminance — any valid color at L=0.72
        # should be visually readable on dark background
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        # Relative luminance should be reasonably high (>0.1 for AA contrast)
        lum = 0.2126 * (r / 255) + 0.7152 * (g / 255) + 0.0722 * (b / 255)
        assert lum > 0.1


class TestOklabToHex:
    def test_neutral_gray(self):
        color = oklab_to_hex(0.5, 0.0, 0.0)
        assert _is_hex(color)

    def test_gamut_clamping(self):
        """Extreme OKLab values should still produce valid hex."""
        color = oklab_to_hex(0.72, 0.3, 0.3)  # beyond extended gamut
        assert _is_hex(color)


class TestEnforceMinimumDeltaE:
    def test_identical_colors_get_separated(self):
        colors = [("a", "#a855f7"), ("b", "#a855f7")]
        result = enforce_minimum_delta_e(colors, min_delta_e=0.04)
        assert result[0][1] != result[1][1]  # no longer identical

    def test_already_distinct_unchanged(self):
        colors = [("a", "#a855f7"), ("b", "#00e5ff")]
        result = enforce_minimum_delta_e(colors, min_delta_e=0.04)
        assert result[0][1] == "#a855f7"
        assert result[1][1] == "#00e5ff"

    def test_empty_input(self):
        assert enforce_minimum_delta_e([], min_delta_e=0.04) == []

    def test_single_color(self):
        colors = [("a", "#ff0000")]
        assert enforce_minimum_delta_e(colors, min_delta_e=0.04) == colors


def test_compute_max_distance_color_returns_valid_hex():
    from app.services.taxonomy.coloring import compute_max_distance_color
    existing = ["#b44aff", "#ff4895", "#36b5ff"]
    result = compute_max_distance_color(existing)
    assert result.startswith("#")
    assert len(result) == 7


def test_compute_max_distance_color_avoids_existing():
    from app.services.taxonomy.coloring import compute_max_distance_color
    existing = ["#b44aff"]
    result = compute_max_distance_color(existing)
    assert result != "#b44aff"


def test_compute_max_distance_color_empty_existing():
    from app.services.taxonomy.coloring import compute_max_distance_color
    result = compute_max_distance_color([])
    assert result.startswith("#")
    assert len(result) == 7
