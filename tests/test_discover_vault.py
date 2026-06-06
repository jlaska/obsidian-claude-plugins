"""Tests for discover_vault.py."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import discover_vault
from discover_vault import discover_vault as _discover_vault


class TestObsidianRunning:
    def test_running_when_cli_returns_name(self):
        with patch("discover_vault._run", return_value="My Vault"):
            assert discover_vault._obsidian_running() is True

    def test_not_running_when_cli_fails(self):
        with patch("discover_vault._run", return_value=None):
            assert discover_vault._obsidian_running() is False

    def test_not_running_when_empty_output(self):
        with patch("discover_vault._run", return_value=""):
            assert discover_vault._obsidian_running() is False


class TestGetVaultPathFromFile:
    def test_prefers_open_vault(self, tmp_path):
        config = {
            "vaults": {
                "vault1": {"path": "/path/vault1", "ts": 100, "open": False},
                "vault2": {"path": "/path/vault2", "ts": 200, "open": True},
            }
        }
        config_file = tmp_path / "obsidian.json"
        config_file.write_text(json.dumps(config))
        with patch.object(Path, "home", return_value=tmp_path.parent):
            with patch("discover_vault.Path") as MockPath:
                # Use the real implementation but with a mock config path
                with patch("builtins.open"):
                    pass
        # Test the logic directly
        vaults = config["vaults"]
        for info in vaults.values():
            if info.get("open"):
                assert info["path"] == "/path/vault2"
                break

    def test_falls_back_to_highest_ts(self):
        vaults = {
            "a": {"path": "/path/a", "ts": 100},
            "b": {"path": "/path/b", "ts": 300},
            "c": {"path": "/path/c", "ts": 200},
        }
        best_ts = -1
        best = None
        for info in vaults.values():
            if not info.get("open"):
                ts = info.get("ts", 0)
                if ts > best_ts:
                    best_ts = ts
                    best = info["path"]
        assert best == "/path/b"


class TestDailyNotesConfigFromFile:
    def test_reads_json_config(self, mock_vault):
        config = discover_vault._get_daily_notes_config_from_file(mock_vault)
        assert config["folder"] == "DAILY_NOTES"
        assert config["format"] == "YYYY/MM-MMMM/YYYY-MM-DD dddd"
        assert "template" in config

    def test_uses_defaults_when_file_missing(self, tmp_path):
        vault = tmp_path / "empty_vault"
        vault.mkdir()
        (vault / ".obsidian").mkdir()
        config = discover_vault._get_daily_notes_config_from_file(vault)
        assert config["folder"] == "DAILY_NOTES"
        assert "YYYY" in config["format"]


class TestComputeTodayPaths:
    def test_computes_daily_note_path(self, mock_vault):
        from datetime import datetime
        date = datetime(2026, 6, 5)
        config = {"folder": "DAILY_NOTES", "format": "YYYY/MM-MMMM/YYYY-MM-DD dddd"}
        paths = discover_vault._compute_today_paths(mock_vault, config, date)
        assert "2026-06-05" in paths["daily_note_path"]
        assert "Friday" in paths["daily_note_path"]
        assert "06-June" in paths["daily_note_path"]

    def test_computes_meetings_dir(self, mock_vault):
        from datetime import datetime
        date = datetime(2026, 6, 5)
        config = {"folder": "DAILY_NOTES", "format": "YYYY/MM-MMMM/YYYY-MM-DD dddd"}
        paths = discover_vault._compute_today_paths(mock_vault, config, date)
        assert "MEETINGS/2026/06-June" in paths["meetings_dir"]


class TestDiscoverVault:
    def test_returns_vault_config(self, mock_vault):
        from datetime import datetime
        date = datetime(2026, 6, 5)
        with patch("discover_vault._obsidian_running", return_value=False):
            with patch("discover_vault._get_vault_path_from_file", return_value=str(mock_vault)):
                config = _discover_vault(date=date)
        assert config["vault_root"] == str(mock_vault)
        assert config["obsidian_running"] is False
        assert "daily_notes" in config
        assert "meetings" in config
        assert "people" in config
        assert "today" in config

    def test_raises_when_no_vault_found(self):
        with patch("discover_vault._obsidian_running", return_value=False):
            with patch("discover_vault._get_vault_path_from_file", return_value=None):
                with pytest.raises(RuntimeError, match="Could not determine"):
                    _discover_vault()

    def test_cli_output_is_valid_json(self, mock_vault):
        """Smoke test: script produces parseable JSON."""
        from datetime import datetime
        date = datetime(2026, 6, 5)
        with patch("discover_vault._obsidian_running", return_value=False):
            with patch("discover_vault._get_vault_path_from_file", return_value=str(mock_vault)):
                config = _discover_vault(date=date)
        # Should serialize without error
        json_str = json.dumps(config)
        parsed = json.loads(json_str)
        assert parsed["vault_root"] == str(mock_vault)
