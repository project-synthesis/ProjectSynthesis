"""R8 (audit 2026-04-27): threshold-collision invariant tests.

The R8 hardening adds a module-level call to _validate_threshold_invariants
in app.services.taxonomy._constants that fails fast if the creation
lower-bound threshold drifts to <= the dissolution floor — preventing the
unrecoverable degenerate state where sub-domains are uncreatable AND
dissolvable simultaneously.
"""
from __future__ import annotations

import pytest

import app.services.taxonomy._constants as _constants


class TestThresholdCollisionInvariant:
    """R8: module-level invariant guarding threshold ordering."""

    def test_default_constants_satisfy_invariant(self):
        """AC-R8-1: shipped values LOW=0.40 > FLOOR=0.25 — module imports clean
        (the import-time call to _validate_threshold_invariants succeeded;
        if it had not, every test would error at collection time).
        """
        assert (
            _constants.SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW
            > _constants.SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR
        )

    def test_invariant_fires_on_violation(self):
        """AC-R8-2: violation triggers AssertionError with helpful message."""
        with pytest.raises(AssertionError, match="Threshold collision"):
            _constants._validate_threshold_invariants(low=0.20, floor=0.25)

    def test_invariant_rejects_equality(self):
        """AC-R8-3: LOW == FLOOR is rejected (strict >, not >=). A freshly
        created sub-domain at exactly the dissolution floor would die on
        the next Phase 5 cycle, defeating the purpose."""
        with pytest.raises(AssertionError):
            _constants._validate_threshold_invariants(low=0.25, floor=0.25)
