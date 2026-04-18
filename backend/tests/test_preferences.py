"""Tests for PreferencesService — file-based JSON preferences with validation."""

import json
from pathlib import Path
from unittest.mock import patch as mock_patch

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


# ── TestForcePassthrough ──────────────────────────────────────────────


class TestForcePassthrough:
    def test_default_is_false(self, svc: PreferencesService) -> None:
        prefs = svc.load()
        assert prefs["pipeline"]["force_passthrough"] is False

    def test_can_be_patched_true(self, svc: PreferencesService) -> None:
        result = svc.patch({"pipeline": {"force_passthrough": True}})
        assert result["pipeline"]["force_passthrough"] is True

    def test_can_be_patched_false(self, svc: PreferencesService) -> None:
        svc.patch({"pipeline": {"force_passthrough": True}})
        result = svc.patch({"pipeline": {"force_passthrough": False}})
        assert result["pipeline"]["force_passthrough"] is False

    def test_non_boolean_rejected_by_validate(self, svc: PreferencesService) -> None:
        prefs = svc.load()
        prefs["pipeline"]["force_passthrough"] = "yes"
        with pytest.raises(ValueError, match="force_passthrough"):
            svc.save(prefs)

    def test_non_boolean_sanitized_to_default(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        import json as _json
        prefs_file.write_text(_json.dumps({
            "schema_version": 1,
            "pipeline": {"force_passthrough": "yes"},
        }))
        prefs = svc.load()
        assert prefs["pipeline"]["force_passthrough"] is False

    def test_missing_key_merges_to_false(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        """Older preferences.json without force_passthrough silently gets False."""
        import json as _json
        prefs_file.write_text(_json.dumps({
            "schema_version": 1,
            "pipeline": {
                "enable_explore": True,
                "enable_scoring": True,
                "enable_adaptation": True,
                "force_sampling": False,
            },
        }))
        prefs = svc.load()
        assert prefs["pipeline"]["force_passthrough"] is False

    def test_get_dot_path(self, svc: PreferencesService) -> None:
        snap = svc.load()
        assert svc.get("pipeline.force_passthrough", snapshot=snap) is False


# ── TestMutualExclusion ───────────────────────────────────────────────


class TestMutualExclusion:
    def test_both_true_raises_value_error(self, svc: PreferencesService) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            svc.patch({"pipeline": {"force_sampling": True, "force_passthrough": True}})

    def test_force_sampling_true_when_passthrough_already_true_raises(
        self, svc: PreferencesService
    ) -> None:
        # Set passthrough first (no conflict yet)
        svc.patch({"pipeline": {"force_passthrough": True}})
        # Patch force_sampling=True — deep-merge produces both=True → raises
        with pytest.raises(ValueError, match="mutually exclusive"):
            svc.patch({"pipeline": {"force_sampling": True}})

    def test_both_false_is_valid(self, svc: PreferencesService) -> None:
        result = svc.patch({"pipeline": {"force_sampling": False, "force_passthrough": False}})
        assert result["pipeline"]["force_sampling"] is False
        assert result["pipeline"]["force_passthrough"] is False

    def test_only_force_sampling_true_valid(self, svc: PreferencesService) -> None:
        result = svc.patch({"pipeline": {"force_sampling": True, "force_passthrough": False}})
        assert result["pipeline"]["force_sampling"] is True
        assert result["pipeline"]["force_passthrough"] is False

    def test_only_force_passthrough_true_valid(self, svc: PreferencesService) -> None:
        result = svc.patch({"pipeline": {"force_passthrough": True, "force_sampling": False}})
        assert result["pipeline"]["force_passthrough"] is True
        assert result["pipeline"]["force_sampling"] is False


# ── TestPreferencesChangedEvent ─────────────────────────────────────


class TestPreferencesChangedEvent:
    async def test_patch_publishes_preferences_changed_event(self, svc: PreferencesService) -> None:
        """PATCH /api/preferences publishes a preferences_changed event."""
        from app.services.event_bus import event_bus

        with mock_patch.object(event_bus, "publish") as mock_publish:
            # Swap in our tmp_path-based service
            with mock_patch("app.routers.preferences._svc", svc):
                from app.routers.preferences import PreferencesUpdate, _ModelsUpdate, patch_preferences
                body = PreferencesUpdate(models=_ModelsUpdate(analyzer="haiku"))
                result = await patch_preferences(body)

            mock_publish.assert_called_once_with("preferences_changed", result)
            assert result["models"]["analyzer"] == "haiku"


# ── TestEffortPreferences ─────────────────────────────────────────────


class TestEffortPreferences:
    """Tests for per-phase effort preference keys."""

    def test_defaults_include_analyzer_and_scorer_effort(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        assert prefs["pipeline"]["analyzer_effort"] == "low"
        assert prefs["pipeline"]["scorer_effort"] == "low"
        assert prefs["pipeline"]["optimizer_effort"] == "high"

    def test_existing_file_gains_new_effort_defaults(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        """Simulates upgrading from a preferences file without effort keys."""
        prefs_file.write_text(json.dumps({
            "schema_version": 1,
            "models": {"analyzer": "sonnet", "optimizer": "opus", "scorer": "sonnet"},
            "pipeline": {"enable_explore": True, "enable_scoring": True,
                         "enable_adaptation": True, "force_sampling": False,
                         "force_passthrough": False, "optimizer_effort": "high"},
            "defaults": {"strategy": "auto"},
        }))
        prefs = svc.load()
        assert prefs["pipeline"]["analyzer_effort"] == "low"
        assert prefs["pipeline"]["scorer_effort"] == "low"

    def test_valid_efforts_accepted_by_patch(
        self, svc: PreferencesService
    ) -> None:
        for effort in ("low", "medium", "high", "max"):
            result = svc.patch({"pipeline": {"analyzer_effort": effort}})
            assert result["pipeline"]["analyzer_effort"] == effort

    def test_valid_efforts_accepted_for_scorer(
        self, svc: PreferencesService
    ) -> None:
        for effort in ("low", "medium", "high", "max"):
            result = svc.patch({"pipeline": {"scorer_effort": effort}})
            assert result["pipeline"]["scorer_effort"] == effort

    def test_optimizer_effort_now_accepts_low_and_medium(
        self, svc: PreferencesService
    ) -> None:
        for effort in ("low", "medium"):
            result = svc.patch({"pipeline": {"optimizer_effort": effort}})
            assert result["pipeline"]["optimizer_effort"] == effort

    def test_invalid_effort_rejected_by_validate(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        prefs["pipeline"]["analyzer_effort"] = "turbo"
        with pytest.raises(ValueError, match="Invalid.*effort.*turbo"):
            svc.save(prefs)

    def test_invalid_scorer_effort_rejected(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        prefs["pipeline"]["scorer_effort"] = "ultra"
        with pytest.raises(ValueError, match="Invalid.*effort.*ultra"):
            svc.save(prefs)

    def test_invalid_effort_sanitized_on_load(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        prefs_file.write_text(json.dumps({
            "schema_version": 1,
            "models": {"analyzer": "sonnet", "optimizer": "opus", "scorer": "sonnet"},
            "pipeline": {"enable_explore": True, "enable_scoring": True,
                         "enable_adaptation": True, "force_sampling": False,
                         "force_passthrough": False, "optimizer_effort": "high",
                         "analyzer_effort": "turbo", "scorer_effort": "warp"},
            "defaults": {"strategy": "auto"},
        }))
        prefs = svc.load()
        # Invalid values replaced with defaults
        assert prefs["pipeline"]["analyzer_effort"] == "low"
        assert prefs["pipeline"]["scorer_effort"] == "low"

    def test_get_dot_path_for_effort(self, svc: PreferencesService) -> None:
        snap = svc.load()
        assert svc.get("pipeline.analyzer_effort", snapshot=snap) == "low"
        assert svc.get("pipeline.scorer_effort", snapshot=snap) == "low"
        assert svc.get("pipeline.optimizer_effort", snapshot=snap) == "high"


# ── TestDomainReadinessNotifications ──────────────────────────────────


class TestDomainReadinessNotifications:
    """Tests for the domain_readiness_notifications preference section."""

    def test_defaults_include_domain_readiness_notifications(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        assert prefs["domain_readiness_notifications"]["enabled"] is False
        assert prefs["domain_readiness_notifications"]["muted_domain_ids"] == []

    def test_existing_file_gains_new_notifications_defaults(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        """Simulates upgrading from a preferences file without the notifications section."""
        prefs_file.write_text(json.dumps({
            "schema_version": 1,
            "models": {"analyzer": "sonnet", "optimizer": "opus", "scorer": "sonnet"},
            "pipeline": {"enable_explore": True, "enable_scoring": True,
                         "enable_adaptation": True, "force_sampling": False,
                         "force_passthrough": False, "optimizer_effort": "high"},
            "defaults": {"strategy": "auto"},
        }))
        prefs = svc.load()
        assert prefs["domain_readiness_notifications"]["enabled"] is False
        assert prefs["domain_readiness_notifications"]["muted_domain_ids"] == []

    def test_patch_can_enable_notifications_and_mute_domain(
        self, svc: PreferencesService
    ) -> None:
        # The section must be a first-class entry in DEFAULTS so callers can
        # rely on its presence without patching (mirrors other top-level sections).
        from app.services.preferences import DEFAULTS
        assert "domain_readiness_notifications" in DEFAULTS
        assert DEFAULTS["domain_readiness_notifications"]["enabled"] is False
        assert DEFAULTS["domain_readiness_notifications"]["muted_domain_ids"] == []

        result = svc.patch({
            "domain_readiness_notifications": {
                "enabled": True,
                "muted_domain_ids": ["abc-123"],
            }
        })
        assert result["domain_readiness_notifications"]["enabled"] is True
        assert result["domain_readiness_notifications"]["muted_domain_ids"] == ["abc-123"]

        # Persistence roundtrip
        reloaded = svc.load()
        assert reloaded["domain_readiness_notifications"]["enabled"] is True
        assert reloaded["domain_readiness_notifications"]["muted_domain_ids"] == ["abc-123"]

    def test_validate_rejects_non_bool_enabled(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        prefs["domain_readiness_notifications"]["enabled"] = "yes"
        with pytest.raises(ValueError, match="domain_readiness_notifications.enabled"):
            svc.save(prefs)

    def test_validate_rejects_non_list_muted_domain_ids(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        prefs["domain_readiness_notifications"]["muted_domain_ids"] = "abc"
        with pytest.raises(ValueError, match="muted_domain_ids"):
            svc.save(prefs)

    def test_validate_rejects_non_string_entries_in_muted_domain_ids(
        self, svc: PreferencesService
    ) -> None:
        prefs = svc.load()
        prefs["domain_readiness_notifications"]["muted_domain_ids"] = [1, 2]
        with pytest.raises(ValueError, match="muted_domain_ids"):
            svc.save(prefs)

    def test_sanitize_replaces_corrupt_enabled_with_default(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        prefs_file.write_text(json.dumps({
            "schema_version": 1,
            "domain_readiness_notifications": {
                "enabled": "yes",
                "muted_domain_ids": [],
            },
        }))
        prefs = svc.load()
        assert prefs["domain_readiness_notifications"]["enabled"] is False

    def test_sanitize_replaces_corrupt_muted_domain_ids_with_default(
        self, svc: PreferencesService, prefs_file: Path
    ) -> None:
        prefs_file.write_text(json.dumps({
            "schema_version": 1,
            "domain_readiness_notifications": {
                "enabled": False,
                "muted_domain_ids": "abc",
            },
        }))
        prefs = svc.load()
        assert prefs["domain_readiness_notifications"]["muted_domain_ids"] == []

    def test_validate_rejects_non_dict_notifications_section(
        self, svc: PreferencesService
    ) -> None:
        """Guards the `not isinstance(notif, dict)` branch of `_validate`."""
        prefs = svc.load()
        prefs["domain_readiness_notifications"] = "garbage"
        with pytest.raises(
            ValueError, match="domain_readiness_notifications.*expected dict"
        ):
            svc.save(prefs)
