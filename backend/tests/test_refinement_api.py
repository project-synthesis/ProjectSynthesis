"""Contract tests for refinement API schemas."""

import pytest
from app.schemas.refinement import RefineRequest, ForkRequest, SelectRequest


class TestRefineRequestValidation:
    def test_valid_refine(self):
        r = RefineRequest(message="Make it shorter")
        assert r.message == "Make it shorter"

    def test_empty_message_rejected(self):
        with pytest.raises(Exception):
            RefineRequest(message="")

    def test_protect_dimensions_optional(self):
        r = RefineRequest(message="Improve clarity", protect_dimensions=["clarity_score"])
        assert r.protect_dimensions == ["clarity_score"]


class TestForkRequestValidation:
    def test_valid_fork(self):
        f = ForkRequest(parent_branch_id="branch-1", message="Try concise version")
        assert f.parent_branch_id == "branch-1"

    def test_label_optional(self):
        f = ForkRequest(parent_branch_id="b-1", message="test", label="concise-v1")
        assert f.label == "concise-v1"


class TestSelectRequestValidation:
    def test_valid_select(self):
        s = SelectRequest(branch_id="branch-1")
        assert s.branch_id == "branch-1"

    def test_reason_optional(self):
        s = SelectRequest(branch_id="b-1", reason="Better structure")
        assert s.reason == "Better structure"
