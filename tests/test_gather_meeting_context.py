"""Tests for gather_meeting_context.py."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import gather_meeting_context as gmc


class TestParseMeetingsTable:
    def test_parses_table_row(self, mock_vault):
        daily_note = (mock_vault / "DAILY_NOTES" / "2026" / "06-June" / "2026-06-05 Friday.md").read_text()
        rows = gmc.parse_meetings_table(daily_note)
        assert len(rows) == 1
        row = rows[0]
        assert row["stem"] == "2026-06-05 - Test User & Alice Tester 1-1"
        assert row["display_title"] == "Test User & Alice Tester 1:1"
        assert row["time"] == "9:00 AM"
        assert "[[Alice Tester]]" in row["attendees"]

    def test_skips_header_row(self):
        content = "# 📅 Meetings\n\n| Time | Meeting | Attendees | Summary |\n|------|---------|-----------|---------|"
        rows = gmc.parse_meetings_table(content)
        assert rows == []

    def test_stops_at_next_heading(self):
        content = (
            "# 📅 Meetings\n\n"
            "| Time | Meeting | Attendees | Summary |\n"
            "|------|---------|-----------|------|\n"
            "| 9:00 AM | [[2026-06-05 - Test Meeting\\|Test]] | [[Alice]] |  |\n"
            "\n# Other Section\n\n"
            "| 10:00 AM | [[2026-06-05 - Another\\|Another]] | [[Bob]] |  |\n"
        )
        rows = gmc.parse_meetings_table(content)
        assert len(rows) == 1
        assert "Test Meeting" in rows[0]["stem"]

    def test_no_meetings_section(self):
        content = "# Daily Note\n\nSome content"
        assert gmc.parse_meetings_table(content) == []


class TestClassifyMeetingTime:
    def test_future_is_upcoming(self):
        future = "2099-06-05T09:00:00-04:00"
        now = datetime.now().astimezone()
        assert gmc.classify_meeting_time(future, now) == "upcoming"

    def test_past_is_past(self):
        past = "2020-01-01T09:00:00-04:00"
        now = datetime.now().astimezone()
        assert gmc.classify_meeting_time(past, now) == "past"

    def test_invalid_date_defaults_to_upcoming(self):
        assert gmc.classify_meeting_time("not-a-date", datetime.now().astimezone()) == "upcoming"


class TestGetMeetingType:
    def test_one_attendee_is_one_on_one(self):
        assert gmc.get_meeting_type(["[[Alice Tester]]"]) == "one_on_one"

    def test_two_attendees_is_group(self):
        assert gmc.get_meeting_type(["[[Alice]]", "[[Bob]]"]) == "group"

    def test_empty_attendees_is_group(self):
        assert gmc.get_meeting_type([]) == "group"


class TestHasMeetingPreparationSection:
    def test_detects_section(self):
        content = "# 📅 Meetings\n\nTable\n\n# Meeting Preparation\n\nCallout"
        assert gmc._has_meeting_preparation_section(content) is True

    def test_returns_false_when_absent(self):
        content = "# 📅 Meetings\n\nTable"
        assert gmc._has_meeting_preparation_section(content) is False


class TestMeetingsNeedingContent:
    def test_returns_stems_with_placeholder(self):
        content = (
            "# Meeting Preparation\n\n"
            "> [!tip]- [[2026-06-09 - Meeting A\\|Meeting A]] (9:00 AM)\n"
            "> **Previous meetings:**\n"
            "> - *(gathering context...)*\n"
            ">\n"
            "> **Suggested topics:**\n"
            "> - *(preparing...)*\n"
        )
        result = gmc._meetings_needing_content(content)
        assert "2026-06-09 - Meeting A" in result

    def test_excludes_callouts_with_real_content(self):
        content = (
            "# Meeting Preparation\n\n"
            "> [!tip]- [[2026-06-09 - Meeting B\\|Meeting B]] (10:00 AM)\n"
            "> **Previous meetings:**\n"
            "> - [[2026-06-01 - Meeting B\\|June 01, 2026]] - Discussed roadmap.\n"
            ">\n"
            "> **Suggested topics:**\n"
            "> - Follow up on roadmap items\n"
        )
        result = gmc._meetings_needing_content(content)
        assert "2026-06-09 - Meeting B" not in result

    def test_returns_empty_when_no_section(self):
        content = "# 📅 Meetings\n\nTable\n"
        result = gmc._meetings_needing_content(content)
        assert result == set()

    def test_mixed_callouts(self):
        content = (
            "# Meeting Preparation\n\n"
            "> [!tip]- [[2026-06-09 - Meeting A\\|Meeting A]] (9:00 AM)\n"
            "> **Previous meetings:**\n"
            "> - *(gathering context...)*\n"
            ">\n"
            "> **Suggested topics:**\n"
            "> - *(preparing...)*\n"
            "\n"
            "> [!tip]- [[2026-06-09 - Meeting B\\|Meeting B]] (10:00 AM)\n"
            "> **Previous meetings:**\n"
            "> - [[2026-06-01 - Meeting B\\|June 01, 2026]] - Reviewed roadmap.\n"
            ">\n"
            "> **Suggested topics:**\n"
            "> - Follow up\n"
        )
        result = gmc._meetings_needing_content(content)
        assert "2026-06-09 - Meeting A" in result
        assert "2026-06-09 - Meeting B" not in result

    def test_needs_content_field_in_gather_context_output(self, mock_vault):
        with patch("gather_meeting_context._obsidian_search", return_value=[]):
            meetings = gmc.gather_context(
                vault_root=mock_vault,
                date=datetime(2026, 6, 5),
                owner_first_name="Test",
            )
        assert len(meetings) > 0
        for m in meetings:
            assert "needs_content" in m
            assert isinstance(m["needs_content"], bool)


class TestExtractAttendeesListFromFile:
    def test_extracts_attendees(self, mock_vault):
        meeting_file = mock_vault / "MEETINGS" / "2026" / "06-June" / "2026-06-05 - Test User & Alice Tester 1-1.md"
        content = meeting_file.read_text()
        attendees = gmc._extract_attendees_list(content)
        assert len(attendees) == 1
        assert "Alice Tester" in attendees[0]

    def test_returns_empty_when_no_frontmatter(self):
        assert gmc._extract_attendees_list("No frontmatter") == []


class TestFindPreviousMeetings:
    def test_finds_previous_via_grep(self, mock_vault):
        # obsidian search won't work in tests (Obsidian not running)
        # grep fallback should find the previous meeting
        with patch("gather_meeting_context._obsidian_search", return_value=[]):
            results = gmc.find_previous_meetings(
                stem="2026-06-05 - Test User & Alice Tester 1-1",
                recurring_id="recurringabc123",
                attendees=["[[Alice Tester]]"],
                meeting_type="one_on_one",
                owner_first_name="Test",
                vault_root=mock_vault,
                limit=3,
            )
        assert len(results) > 0
        assert any("2026-05-20" in str(r) for r in results)

    def test_excludes_today_file(self, mock_vault):
        with patch("gather_meeting_context._obsidian_search", return_value=[]):
            results = gmc.find_previous_meetings(
                stem="2026-06-05 - Test User & Alice Tester 1-1",
                recurring_id="recurringabc123",
                attendees=["[[Alice Tester]]"],
                meeting_type="one_on_one",
                owner_first_name="Test",
                vault_root=mock_vault,
            )
        stems = [r.stem for r in results]
        assert "2026-06-05 - Test User & Alice Tester 1-1" not in stems


class TestGatherContext:
    def test_full_context(self, mock_vault):
        with patch("gather_meeting_context._obsidian_search", return_value=[]):
            meetings = gmc.gather_context(
                vault_root=mock_vault,
                date=datetime(2026, 6, 5),
                owner_first_name="Test",
            )
        assert isinstance(meetings, list)
        assert len(meetings) == 1

        meeting = meetings[0]
        assert meeting["stem"] == "2026-06-05 - Test User & Alice Tester 1-1"
        assert meeting["type"] == "one_on_one"
        assert meeting["status"] == "past"
        assert "is_first_run" in meeting
        assert len(meeting["previous_meetings"]) > 0
        assert len(meeting["parking_lot"]) > 0

    def test_previous_meeting_has_context(self, mock_vault):
        with patch("gather_meeting_context._obsidian_search", return_value=[]):
            meetings = gmc.gather_context(
                vault_root=mock_vault,
                date=datetime(2026, 6, 5),
                owner_first_name="Test",
            )
        prev = meetings[0]["previous_meetings"][0]
        assert prev["gemini_summary"] != ""
        assert "Q2 priorities" in prev["gemini_summary"]
        assert "- [ ]" in prev["actions_text"] or "- [x]" in prev["actions_text"]

    def test_missing_daily_note_returns_empty_list(self, mock_vault):
        meetings = gmc.gather_context(
            vault_root=mock_vault,
            date=datetime(2025, 1, 1),  # No daily note for this date
            owner_first_name="Test",
        )
        assert meetings == []

    def test_output_is_json_serializable(self, mock_vault):
        with patch("gather_meeting_context._obsidian_search", return_value=[]):
            meetings = gmc.gather_context(
                vault_root=mock_vault,
                date=datetime(2026, 6, 5),
                owner_first_name="Test",
            )
        json_str = json.dumps(meetings)
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert parsed[0]["stem"] == "2026-06-05 - Test User & Alice Tester 1-1"
