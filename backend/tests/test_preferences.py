"""Tests for PreferencesService — file-based JSON preferences with validation."""

import json
from pathlib import Path

import pytest

from app.services.preferences import PreferencesService


@pytest.fixture
def svc(tmp_path: Path) -> PreferencesService:
    """PreferencesService wired to a temporary directory."""
    return PreferencesService(data_dir=tmp_path)


@pytest.fixture
def prefs_file(tmp_path: Path) -> Path:
    return tmp_path / "preferences.json"


# ── TestLoad ─────────────────────────────────────────────────────────


class TestLoad:
    def test_defaults_when_no_file(self, svc: PreferencesService) -> None:
        prefs = svc.load()
        assert prefs["schema_version"] == 1
        assert prefs["models"]["analyzer"] == "sonnet"
        assert prefs["models"]["optimizer"] == "opus"
        assert prefs["models"]["scorer"] == "sonnet"
        assert prefs["pipeline"]["enable_explore"] is True
        assert prefs["pipeline"]["enable_scoring"] is True
        assert prefs["pipeline"]["enable_adaptation"] is True
        assert prefs["defaults"]["strategy"] == "auto"

    def test_creates_file_on_first_access(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        assert not prefs_file.exists()
        svc.load()
        assert prefs_file.exists()
        data = json.loads(prefs_file.read_text())
        assert data["schema_version"] == 1

    def test_merges_missing_keys(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        # File exists but is missing the pipeline section
        prefs_file.write_text(json.dumps({"schema_version": 1, "models": {"analyzer": "haiku"}}))
        prefs = svc.load()
        assert prefs["models"]["analyzer"] == "haiku"
        # Missing keys filled from defaults
        assert prefs["pipeline"]["enable_explore"] is True
        assert prefs["defaults"]["strategy"] == "auto"

    def test_recovers_from_corrupt_json(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        prefs_file.write_text("{not valid json!!")
        prefs = svc.load()
        # Falls back to defaults
        assert prefs["schema_version"] == 1
        assert prefs["models"]["analyzer"] == "sonnet"

    def test_replaces_invalid_model(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        prefs_file.write_text(
            json.dumps({
                "schema_version": 1,
                "models": {"analyzer": "gpt-4", "optimizer": "opus", "scorer": "sonnet"},
            })
        )
        prefs = svc.load()
        # Invalid "gpt-4" replaced with default "sonnet"
        assert prefs["models"]["analyzer"] == "sonnet"

    def test_returns_snapshot_copy(self, svc: PreferencesService) -> None:
        """Each load() returns a new dict — mutation doesn't affect next load."""
        snap1 = svc.load()
        snap1["models"]["analyzer"] = "haiku"
        snap2 = svc.load()
        assert snap2["models"]["analyzer"] == "sonnet"


# ── TestSave ─────────────────────────────────────────────────────────


class TestSave:
    def test_writes_valid_json(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        prefs = svc.load()
        prefs["models"]["analyzer"] = "haiku"
        svc.save(prefs)
        data = json.loads(prefs_file.read_text())
        assert data["models"]["analyzer"] == "haiku"

    def test_rejects_invalid_model(self, svc: PreferencesService) -> None:
        prefs = svc.load()
        prefs["models"]["analyzer"] = "invalid-model"
        with pytest.raises(ValueError, match="Invalid model"):
            svc.save(prefs)


# ── TestPatch ────────────────────────────────────────────────────────


class TestPatch:
    def test_deep_merges(self, svc: PreferencesService) -> None:
        result = svc.patch({"models": {"analyzer": "haiku"}})
        assert result["models"]["analyzer"] == "haiku"
        # Other model values preserved
        assert result["models"]["optimizer"] == "opus"
        assert result["models"]["scorer"] == "sonnet"

    def test_rejects_invalid_strategy(self, svc: PreferencesService) -> None:
        with pytest.raises(ValueError, match="Invalid strategy"):
            svc.patch({"defaults": {"strategy": "nonexistent"}})


# ── TestResolveModel ─────────────────────────────────────────────────


class TestResolveModel:
    def test_sonnet(self, svc: PreferencesService) -> None:
        from app.config import settings

        snap = svc.load()
        assert svc.resolve_model("analyzer", snapshot=snap) == settings.MODEL_SONNET

    def test_opus(self, svc: PreferencesService) -> None:
        from app.config import settings

        snap = svc.load()
        assert svc.resolve_model("optimizer", snapshot=snap) == settings.MODEL_OPUS

    def test_haiku(self, svc: PreferencesService) -> None:
        from app.config import settings

        snap = svc.load()
        snap["models"]["scorer"] = "haiku"
        assert svc.resolve_model("scorer", snapshot=snap) == settings.MODEL_HAIKU

    def test_without_snapshot_reads_file(self, svc: PreferencesService) -> None:
        from app.config import settings

        svc.load()  # ensure file exists
        model_id = svc.resolve_model("analyzer")
        assert model_id == settings.MODEL_SONNET


# ── TestGet ──────────────────────────────────────────────────────────


class TestGet:
    def test_dot_path_accessor(self, svc: PreferencesService) -> None:
        snap = svc.load()
        assert svc.get("models.analyzer", snapshot=snap) == "sonnet"
        assert svc.get("pipeline.enable_explore", snapshot=snap) is True
        assert svc.get("defaults.strategy", snapshot=snap) == "auto"

    def test_returns_none_for_missing_path(self, svc: PreferencesService) -> None:
        snap = svc.load()
        assert svc.get("nonexistent.key", snapshot=snap) is None

    def test_reads_disk_without_snapshot(self, svc: PreferencesService) -> None:
        svc.load()  # ensure file exists
        assert svc.get("models.optimizer") == "opus"


# ── TestFileRecovery ─────────────────────────────────────────────────


class TestFileRecovery:
    def test_deleted_file_regenerates_defaults(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        svc.load()  # creates file
        assert prefs_file.exists()
        prefs_file.unlink()
        assert not prefs_file.exists()
        prefs = svc.load()
        assert prefs_file.exists()
        assert prefs["schema_version"] == 1
        assert prefs["models"]["analyzer"] == "sonnet"


# ── TestForceSampling ─────────────────────────────────────────────────


class TestForceSampling:
    def test_default_is_false(self, svc: PreferencesService) -> None:
        prefs = svc.load()
        assert prefs["pipeline"]["force_sampling"] is False

    def test_can_be_patched_true(self, svc: PreferencesService) -> None:
        result = svc.patch({"pipeline": {"force_sampling": True}})
        assert result["pipeline"]["force_sampling"] is True

    def test_can_be_patched_false(self, svc: PreferencesService) -> None:
        svc.patch({"pipeline": {"force_sampling": True}})
        result = svc.patch({"pipeline": {"force_sampling": False}})
        assert result["pipeline"]["force_sampling"] is False

    def test_non_boolean_rejected_by_validate(self, svc: PreferencesService) -> None:
        prefs = svc.load()
        prefs["pipeline"]["force_sampling"] = "yes"
        with pytest.raises(ValueError, match="force_sampling"):
            svc.save(prefs)

    def test_non_boolean_sanitized_to_default(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        import json as _json
        prefs_file.write_text(_json.dumps({
            "schema_version": 1,
            "pipeline": {"force_sampling": "yes"},
        }))
        prefs = svc.load()
        assert prefs["pipeline"]["force_sampling"] is False

    def test_missing_key_merges_to_false(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        """Older preferences.json without force_sampling silently gets False."""
        import json as _json
        prefs_file.write_text(_json.dumps({
            "schema_version": 1,
            "pipeline": {
                "enable_explore": True,
                "enable_scoring": True,
                "enable_adaptation": True,
            },
        }))
        prefs = svc.load()
        assert prefs["pipeline"]["force_sampling"] is False

    def test_get_dot_path(self, svc: PreferencesService) -> None:
        snap = svc.load()
        assert svc.get("pipeline.force_sampling", snapshot=snap) is False
