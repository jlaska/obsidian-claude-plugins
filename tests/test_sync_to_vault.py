"""Tests for sync_meeting_prep_section() in sync_to_vault.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import sync_to_vault as stv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daily_note(meetings_block: str, prep_block: str, trailer: str = "") -> str:
    return (
        "---\ncreated: 2026-06-09\ntags:\n  - Daily_Notes\n---\n\n"
        "# 📅 Meetings\n\n"
        f"{meetings_block}\n\n"
        "# Meeting Preparation\n"
        f"{prep_block}"
        f"{trailer}"
    )


CALLOUT_A = (
    "> [!tip]- [[2026-06-09 - Meeting A\\|Meeting A]] (9:00 AM)\n"
    "> **Previous meetings:**\n"
    "> - None found\n"
    ">\n"
    "> **Suggested topics:**\n"
    "> - Topic A\n"
)

CALLOUT_B = (
    "> [!tip]- [[2026-06-09 - Meeting B\\|Meeting B]] (10:00 AM)\n"
    "> **Previous meetings:**\n"
    "> - None found\n"
    ">\n"
    "> **Suggested topics:**\n"
    "> - Topic B\n"
)

CALLOUT_C_PLACEHOLDER = (
    "> [!tip]- [[2026-06-09 - Meeting C\\|Meeting C]] (11:00 AM)\n"
    "> **Previous meetings:**\n"
    "> - *(gathering context...)*\n"
    ">\n"
    "> **Suggested topics:**\n"
    "> - *(preparing...)*\n"
)

FM_A = {"start": "2026-06-09T09:00:00-04:00", "end": "2026-06-09T10:00:00-04:00", "attendees": []}
FM_B = {"start": "2026-06-09T10:00:00-04:00", "end": "2026-06-09T11:00:00-04:00", "attendees": []}
FM_C = {"start": "2026-06-09T11:00:00-04:00", "end": "2026-06-09T12:00:00-04:00", "attendees": []}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSyncMeetingPrepSection:

    def test_noop_when_no_meeting_preparation_section(self):
        content = "# 📅 Meetings\n\n| Time | Meeting |\n|------|------|\n"
        valid = {"2026-06-09 - Meeting A"}
        stale: set = set()
        rows = [("2026-06-09T09:00:00-04:00", "2026-06-09 - Meeting A", FM_A)]
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        assert result == content

    def test_noop_when_no_adds_or_removes(self):
        content = _make_daily_note("", "\n" + CALLOUT_A)
        valid = {"2026-06-09 - Meeting A"}
        stale: set = set()
        rows = [(FM_A["start"], "2026-06-09 - Meeting A", FM_A)]
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        assert result == content

    def test_removes_stale_callout(self):
        content = _make_daily_note("", "\n" + CALLOUT_A + "\n" + CALLOUT_B)
        valid = {"2026-06-09 - Meeting A"}
        stale = {"2026-06-09 - Meeting B"}
        rows = [(FM_A["start"], "2026-06-09 - Meeting A", FM_A)]
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        assert "Meeting B" not in result
        assert "Meeting A" in result

    def test_inserts_placeholder_for_new_meeting(self):
        content = _make_daily_note("", "\n" + CALLOUT_A)
        valid = {"2026-06-09 - Meeting A", "2026-06-09 - Meeting C"}
        stale: set = set()
        rows = [
            (FM_A["start"], "2026-06-09 - Meeting A", FM_A),
            (FM_C["start"], "2026-06-09 - Meeting C", FM_C),
        ]
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        assert "*(preparing...)*" in result
        assert "Meeting C" in result

    def test_placeholder_inserted_in_time_order(self):
        # B already exists; A and C are new — A should come before B, C after B
        content = _make_daily_note("", "\n" + CALLOUT_B)
        valid = {
            "2026-06-09 - Meeting A",
            "2026-06-09 - Meeting B",
            "2026-06-09 - Meeting C",
        }
        stale: set = set()
        rows = [
            (FM_A["start"], "2026-06-09 - Meeting A", FM_A),
            (FM_B["start"], "2026-06-09 - Meeting B", FM_B),
            (FM_C["start"], "2026-06-09 - Meeting C", FM_C),
        ]
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        pos_a = result.find("Meeting A")
        pos_b = result.find("Meeting B")
        pos_c = result.find("Meeting C")
        assert pos_a < pos_b < pos_c

    def test_all_callouts_removed_leaves_empty_section(self):
        content = _make_daily_note("", "\n" + CALLOUT_A)
        valid: set = set()
        stale = {"2026-06-09 - Meeting A"}
        rows: list = []
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        assert "Meeting A" not in result
        assert "# Meeting Preparation" in result

    def test_content_after_section_preserved(self):
        trailer = "\n# Other Section\n\nSome content\n"
        content = _make_daily_note("", "\n" + CALLOUT_A, trailer)
        valid = {"2026-06-09 - Meeting A"}
        stale: set = set()
        rows = [(FM_A["start"], "2026-06-09 - Meeting A", FM_A)]
        result = stv.sync_meeting_prep_section(content, valid, stale, rows)
        assert "# Other Section" in result
        assert "Some content" in result
