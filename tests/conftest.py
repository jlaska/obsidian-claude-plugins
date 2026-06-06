"""Shared pytest fixtures for obsidian-claude-plugins tests."""

import json
import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VAULT_FIXTURE_DIR = FIXTURES_DIR / "vault"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def mock_vault(tmp_path):
    """Create a minimal Obsidian vault by copying fixture vault into tmp_path."""
    vault = tmp_path / "vault"
    shutil.copytree(VAULT_FIXTURE_DIR, vault)
    return vault


@pytest.fixture
def sample_events():
    """Load fixture calendar events JSON."""
    with open(FIXTURES_DIR / "calendar_events.json") as f:
        return json.load(f)


@pytest.fixture
def sample_events_filtered(sample_events):
    """Only the events that should pass the filter (accepted meetings with others)."""
    return [
        e for e in sample_events["events"]
        if e["summary"] in ("Test User & Alice Tester 1:1", "Team Standup")
    ]


@pytest.fixture
def gog_whoami_json():
    """Load fixture gog whoami JSON."""
    with open(FIXTURES_DIR / "gog_whoami.json") as f:
        return f.read()


@pytest.fixture
def gog_auth_list_text():
    """Load fixture gog auth list plain text."""
    return (FIXTURES_DIR / "gog_auth_list.txt").read_text()


@pytest.fixture
def user_emails():
    """Test user email set."""
    return {"testuser@work.example.com", "testuser@personal.example.com"}


@pytest.fixture
def mock_gog_subprocess(monkeypatch, gog_whoami_json, gog_auth_list_text, sample_events):
    """Mock all gog CLI subprocess calls with fixture data."""
    import subprocess

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        result = subprocess.CompletedProcess(cmd, 0)
        result.stderr = ""

        if "auth" in cmd_str and "list" in cmd_str:
            result.stdout = gog_auth_list_text
        elif "whoami" in cmd_str:
            result.stdout = gog_whoami_json
        elif "calendar" in cmd_str and "events" in cmd_str:
            result.stdout = json.dumps(sample_events)
        elif "people" in cmd_str and "search" in cmd_str:
            result.stdout = json.dumps({"people": []})
        else:
            result.returncode = 1
            result.stdout = ""

        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    return fake_run


@pytest.fixture
def mock_obsidian_cli(monkeypatch, tmp_path):
    """Mock obsidian CLI commands to simulate Obsidian not running."""
    import subprocess

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        result = subprocess.CompletedProcess(cmd, 1)
        result.stdout = ""
        result.stderr = "obsidian: command not found"

        if cmd[0] == "obsidian":
            result.returncode = 1
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    return fake_run
