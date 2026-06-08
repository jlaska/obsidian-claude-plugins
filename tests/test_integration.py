"""Integration tests for the daily-planner script pipeline.

These tests exercise each script's CLI entrypoint and the end-to-end pipeline
flow. They use the mock_vault fixture (a writable copy of the fixture vault) and
mock gog/obsidian subprocess calls so no real network access is needed.

Run with: make test-integration
"""

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

SKILL_DIR = Path(__file__).parent.parent / "skills" / "daily-planner"


def _load(name: str):
    """Load a skill script as a module without executing its __main__ block."""
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: discover_events main() — outputs a flat JSON array
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDiscoverEventsMain:
    def test_outputs_flat_json_array(
        self, tmp_path, self_json_file, mock_gog_subprocess
    ):
        de = _load("discover_events")

        buf = io.StringIO()
        with redirect_stdout(buf):
            de.main.__wrapped__ = None  # in case of decorators
            sys.argv = [
                "discover_events.py",
                "--self-json", str(self_json_file),
                "--cache-dir", str(tmp_path),
                "--date", "2026-06-05",
            ]
            de.main()

        output = buf.getvalue()
        events = json.loads(output)

        assert isinstance(events, list), "Output should be a flat array"
        assert len(events) > 0, "Should have at least one event after filtering"
        for e in events:
            assert "summary" in e
            assert "start" in e

    def test_filters_declined_and_cancelled(
        self, tmp_path, self_json_file, mock_gog_subprocess
    ):
        de = _load("discover_events")

        buf = io.StringIO()
        with redirect_stdout(buf):
            sys.argv = [
                "discover_events.py",
                "--self-json", str(self_json_file),
                "--cache-dir", str(tmp_path),
                "--date", "2026-06-05",
            ]
            de.main()

        events = json.loads(buf.getvalue())
        summaries = {e["summary"] for e in events}

        assert "Test User & Alice Tester 1:1" in summaries
        assert "Team Standup" in summaries
        assert "Declined Meeting" not in summaries
        assert "Cancelled Meeting" not in summaries
        assert "Working Location" not in summaries


# ---------------------------------------------------------------------------
# Test 2: sync_to_vault main() — creates meeting files and updates daily note
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSyncToVaultMain:
    def test_creates_meeting_files(
        self, mock_vault, events_json_file, self_json_file, mock_gog_subprocess
    ):
        stv = _load("sync_to_vault")

        buf = io.StringIO()
        with redirect_stdout(buf):
            sys.argv = [
                "sync_to_vault.py",
                "--vault-root", str(mock_vault),
                "--events-json", str(events_json_file),
                "--self-json", str(self_json_file),
                "--date", "2026-06-05",
            ]
            stv.main()

        output = buf.getvalue()
        assert "Sync complete" in output

        meetings_dir = mock_vault / "MEETINGS" / "2026" / "06-June"
        created = list(meetings_dir.glob("2026-06-05 - *.md"))
        assert len(created) >= 1, f"Expected meeting files, found: {[f.name for f in created]}"

    def test_meeting_frontmatter_has_required_fields(
        self, mock_vault, events_json_file, self_json_file, mock_gog_subprocess
    ):
        stv = _load("sync_to_vault")

        with redirect_stdout(io.StringIO()):
            sys.argv = [
                "sync_to_vault.py",
                "--vault-root", str(mock_vault),
                "--events-json", str(events_json_file),
                "--self-json", str(self_json_file),
                "--date", "2026-06-05",
            ]
            stv.main()

        meetings_dir = mock_vault / "MEETINGS" / "2026" / "06-June"
        one_on_one = next(
            (f for f in meetings_dir.glob("*.md") if "Alice" in f.name), None
        )
        assert one_on_one is not None, "Expected a meeting file for Alice"

        content = one_on_one.read_text()
        assert "attendees:" in content
        assert "start:" in content
        assert "gmeet:" in content

    def test_updates_daily_note_meetings_table(
        self, mock_vault, events_json_file, self_json_file, mock_gog_subprocess
    ):
        stv = _load("sync_to_vault")

        with redirect_stdout(io.StringIO()):
            sys.argv = [
                "sync_to_vault.py",
                "--vault-root", str(mock_vault),
                "--events-json", str(events_json_file),
                "--self-json", str(self_json_file),
                "--date", "2026-06-05",
            ]
            stv.main()

        daily_note = mock_vault / "DAILY_NOTES" / "2026" / "06-June" / "2026-06-05 Friday.md"
        assert daily_note.exists(), "Daily note should exist"
        content = daily_note.read_text()
        assert "# 📅 Meetings" in content
        assert "Alice" in content or "Test User" in content


# ---------------------------------------------------------------------------
# Test 3: gather_meeting_context main() — outputs flat array with is_first_run
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGatherMeetingContextMain:
    def test_outputs_flat_array(self, mock_vault):
        gmc = _load("gather_meeting_context")

        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = type("R", (), {
                "returncode": 1, "stdout": "", "stderr": ""
            })()
            sys.argv = [
                "gather_meeting_context.py",
                "--vault-root", str(mock_vault),
                "--owner-first-name", "Test",
                "--date", "2026-06-05",
            ]
            gmc.main()

        meetings = json.loads(buf.getvalue())
        assert isinstance(meetings, list), "Output should be a flat array"
        assert len(meetings) >= 1

    def test_each_meeting_has_is_first_run(self, mock_vault):
        gmc = _load("gather_meeting_context")

        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = type("R", (), {
                "returncode": 1, "stdout": "", "stderr": ""
            })()
            sys.argv = [
                "gather_meeting_context.py",
                "--vault-root", str(mock_vault),
                "--owner-first-name", "Test",
                "--date", "2026-06-05",
            ]
            gmc.main()

        meetings = json.loads(buf.getvalue())
        for m in meetings:
            assert "is_first_run" in m, f"Missing is_first_run in meeting {m.get('stem')}"
            assert "stem" in m
            assert "status" in m
            assert "type" in m

    def test_missing_daily_note_returns_empty_list(self, mock_vault):
        gmc = _load("gather_meeting_context")

        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = type("R", (), {
                "returncode": 1, "stdout": "", "stderr": ""
            })()
            sys.argv = [
                "gather_meeting_context.py",
                "--vault-root", str(mock_vault),
                "--owner-first-name", "Test",
                "--date", "2025-01-01",  # no daily note for this date
            ]
            gmc.main()

        result = json.loads(buf.getvalue())
        assert result == []


# ---------------------------------------------------------------------------
# Test 4: End-to-end pipeline — discover → sync → gather
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestEndToEndPipeline:
    def test_pipeline_produces_meeting_context(
        self, tmp_path, mock_vault, self_json_file, mock_gog_subprocess
    ):
        """Full pipeline: discover events → sync to vault → gather context."""
        de = _load("discover_events")
        stv = _load("sync_to_vault")
        gmc = _load("gather_meeting_context")

        # Step 1: discover_events → events.json
        events_json = tmp_path / "events.json"
        buf = io.StringIO()
        with redirect_stdout(buf):
            sys.argv = [
                "discover_events.py",
                "--self-json", str(self_json_file),
                "--cache-dir", str(tmp_path),
                "--date", "2026-06-05",
            ]
            de.main()
        events_json.write_text(buf.getvalue())
        events = json.loads(buf.getvalue())
        assert isinstance(events, list) and len(events) > 0

        # Step 2: sync_to_vault → creates meeting files + daily note
        with redirect_stdout(io.StringIO()):
            sys.argv = [
                "sync_to_vault.py",
                "--vault-root", str(mock_vault),
                "--events-json", str(events_json),
                "--self-json", str(self_json_file),
                "--date", "2026-06-05",
            ]
            stv.main()

        # Step 3: gather_meeting_context → meeting context
        buf = io.StringIO()
        with redirect_stdout(buf), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = type("R", (), {
                "returncode": 1, "stdout": "", "stderr": ""
            })()
            sys.argv = [
                "gather_meeting_context.py",
                "--vault-root", str(mock_vault),
                "--owner-first-name", "Test",
                "--date", "2026-06-05",
            ]
            gmc.main()

        meetings = json.loads(buf.getvalue())
        assert isinstance(meetings, list)
        assert len(meetings) > 0

        stems = {m["stem"] for m in meetings}
        # The 1:1 event from fixtures should be reflected in context
        assert any("Alice" in s or "1-1" in s.lower() for s in stems), \
            f"Expected 1:1 meeting in context, got: {stems}"

        # Every meeting must have is_first_run
        for m in meetings:
            assert "is_first_run" in m
