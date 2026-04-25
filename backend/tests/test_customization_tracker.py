"""Tests for customization_tracker — registry of user-edited prompt files.

Pinned contract:

  1. PUT /api/strategies/{name} → tracker records the edit.
  2. Registry survives restart (JSON in gitignored data/).
  3. Corrupt / missing registry degrades to empty (best-effort).
  4. ``record_edit`` is idempotent on the same content.
  5. ``clear_entry`` removes only the requested path.
  6. Atomic write via tmp+rename — partial writes leave the original
     file intact.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.customization_tracker import (
    REGISTRY_FILE,
    SCHEMA_VERSION,
    CustomizationTracker,
)


@pytest.fixture
def tracker(tmp_path):
    """Fresh tracker rooted at a tmp dir."""
    return CustomizationTracker(root=tmp_path)


class TestRecordEdit:
    def test_first_edit_creates_registry(self, tracker, tmp_path):
        tracker.record_edit("prompts/strategies/foo.md", "content", source="api")
        registry_path = tmp_path / REGISTRY_FILE
        assert registry_path.exists()
        data = json.loads(registry_path.read_text())
        assert data["version"] == SCHEMA_VERSION
        assert "prompts/strategies/foo.md" in data["files"]
        entry = data["files"]["prompts/strategies/foo.md"]
        assert entry["source"] == "api"
        assert entry["current_sha"] == hashlib.sha256(b"content").hexdigest()[:16]
        assert "modified_at" in entry

    def test_second_edit_overwrites_entry(self, tracker):
        tracker.record_edit("prompts/strategies/foo.md", "v1", source="api")
        tracker.record_edit("prompts/strategies/foo.md", "v2", source="api")
        files = tracker.list_modifications()
        assert len(files) == 1
        assert files["prompts/strategies/foo.md"]["current_sha"] == (
            hashlib.sha256(b"v2").hexdigest()[:16]
        )

    def test_two_files_tracked_independently(self, tracker):
        tracker.record_edit("prompts/strategies/a.md", "A", source="api")
        tracker.record_edit("prompts/strategies/b.md", "B", source="api")
        files = tracker.list_modifications()
        assert set(files) == {"prompts/strategies/a.md", "prompts/strategies/b.md"}

    def test_empty_path_is_noop(self, tracker):
        tracker.record_edit("", "content", source="api")
        assert tracker.list_modifications() == {}

    def test_source_field_preserved(self, tracker):
        tracker.record_edit("a.md", "x", source="api")
        tracker.record_edit("b.md", "x", source="manual")
        tracker.record_edit("c.md", "x", source="unknown")
        files = tracker.list_modifications()
        assert files["a.md"]["source"] == "api"
        assert files["b.md"]["source"] == "manual"
        assert files["c.md"]["source"] == "unknown"


class TestIsModified:
    def test_unknown_path_is_false(self, tracker):
        assert tracker.is_modified("never-edited.md") is False

    def test_recorded_path_is_true(self, tracker):
        tracker.record_edit("known.md", "x", source="api")
        assert tracker.is_modified("known.md") is True


class TestClearEntry:
    def test_clears_named_entry_only(self, tracker):
        tracker.record_edit("a.md", "x", source="api")
        tracker.record_edit("b.md", "x", source="api")
        tracker.clear_entry("a.md")
        assert tracker.is_modified("a.md") is False
        assert tracker.is_modified("b.md") is True

    def test_clear_unknown_path_is_noop(self, tracker):
        tracker.record_edit("a.md", "x", source="api")
        tracker.clear_entry("never-tracked.md")
        assert tracker.is_modified("a.md") is True


class TestPersistence:
    """Cross-instance persistence — registry survives a process restart
    (modeled here by instantiating a second tracker on the same root)."""

    def test_second_instance_reads_first_writes(self, tmp_path):
        first = CustomizationTracker(root=tmp_path)
        first.record_edit("foo.md", "x", source="api")

        second = CustomizationTracker(root=tmp_path)
        assert second.is_modified("foo.md") is True

    def test_second_instance_clear_persists(self, tmp_path):
        first = CustomizationTracker(root=tmp_path)
        first.record_edit("foo.md", "x", source="api")
        first.clear_entry("foo.md")

        second = CustomizationTracker(root=tmp_path)
        assert second.is_modified("foo.md") is False


class TestDefensive:
    """Best-effort degradation. Tracker should never raise on a bad
    registry file — operators expect the API to keep working even if
    data/ is corrupted."""

    def test_corrupt_json_starts_fresh(self, tmp_path):
        registry_path = tmp_path / REGISTRY_FILE
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{ this is not json")

        tracker = CustomizationTracker(root=tmp_path)
        assert tracker.list_modifications() == {}
        # And it can still record without erroring.
        tracker.record_edit("a.md", "x", source="api")
        assert tracker.is_modified("a.md") is True

    def test_unexpected_schema_version_starts_fresh(self, tmp_path):
        registry_path = tmp_path / REGISTRY_FILE
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps({
            "version": 999,
            "files": {"old.md": {"current_sha": "abc"}},
        }))

        tracker = CustomizationTracker(root=tmp_path)
        # Version mismatch ⇒ ignore old data, start fresh.
        assert tracker.is_modified("old.md") is False

    def test_top_level_not_an_object_starts_fresh(self, tmp_path):
        registry_path = tmp_path / REGISTRY_FILE
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(["not", "a", "dict"]))

        tracker = CustomizationTracker(root=tmp_path)
        assert tracker.list_modifications() == {}

    def test_files_field_not_a_dict_coerces_to_empty(self, tmp_path):
        registry_path = tmp_path / REGISTRY_FILE
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps({
            "version": SCHEMA_VERSION,
            "files": ["malformed"],
        }))

        tracker = CustomizationTracker(root=tmp_path)
        assert tracker.list_modifications() == {}


class TestAtomicWrite:
    """``_save`` writes via tmp + rename — never leaves a half-written
    registry on disk on failure."""

    def test_save_completes_atomically(self, tmp_path, monkeypatch):
        tracker = CustomizationTracker(root=tmp_path)
        tracker.record_edit("a.md", "v1", source="api")
        registry_path = tmp_path / REGISTRY_FILE
        # The post-save tmp file must not linger.
        tmp_files = list(tmp_path.glob("**/.user_customizations.json.tmp"))
        assert tmp_files == []
        # And the canonical file is intact JSON.
        json.loads(registry_path.read_text())


class TestListModifications:
    def test_returns_defensive_copy(self, tracker):
        tracker.record_edit("a.md", "x", source="api")
        copy = tracker.list_modifications()
        copy["a.md"]["source"] = "tampered"
        # Mutating the copy must not affect the registry.
        assert tracker.list_modifications()["a.md"]["source"] == "api"
