"""Tests for discover_events.py."""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import discover_events


USER_EMAILS = {"testuser@work.example.com", "testuser@personal.example.com"}


class TestFetchAccountEventsDateFlags:
    """Verify the correct gog CLI flags are used for today vs. other dates."""

    def _make_result(self, events):
        r = subprocess.CompletedProcess([], 0)
        r.stdout = json.dumps({"events": events})
        r.stderr = ""
        return r

    def test_uses_today_flag_for_current_date(self):
        account = {"email": "testuser@work.example.com", "client": "default"}
        today = datetime.now()
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._make_result([])

        with patch("discover_events.subprocess.run", side_effect=fake_run):
            discover_events.fetch_account_events(account, today)

        assert len(captured) == 1
        assert "--today" in captured[0]
        assert not any("--from" in str(a) or "--time-min" in str(a) for a in captured[0])

    def test_uses_from_to_flags_for_past_date(self):
        account = {"email": "testuser@work.example.com", "client": "default"}
        past = datetime(2026, 6, 5)
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._make_result([])

        with patch("discover_events.subprocess.run", side_effect=fake_run):
            discover_events.fetch_account_events(account, past)

        assert len(captured) == 1
        cmd = captured[0]
        assert "--today" not in cmd
        assert any("--from=2026-06-05" in str(a) for a in cmd), f"--from flag missing in {cmd}"
        assert any("--to=2026-06-06" in str(a) for a in cmd), f"--to flag missing in {cmd}"
        # Explicitly verify the wrong flags are not used
        assert not any("--time-min" in str(a) or "--time-max" in str(a) for a in cmd)

    def test_default_uses_primary_calendar_only(self):
        account = {"email": "testuser@work.example.com", "client": "default"}
        today = datetime.now()
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._make_result([])

        with patch("discover_events.subprocess.run", side_effect=fake_run):
            discover_events.fetch_account_events(account, today)

        assert "--all" not in captured[0]
        assert "--cal" not in captured[0]

    def test_all_calendars_flag_passes_all(self):
        account = {"email": "testuser@work.example.com", "client": "default"}
        today = datetime.now()
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._make_result([])

        with patch("discover_events.subprocess.run", side_effect=fake_run):
            discover_events.fetch_account_events(account, today, all_calendars=True)

        assert "--all" in captured[0]

    def test_calendars_flag_passes_cal_ids(self):
        account = {"email": "testuser@work.example.com", "client": "default"}
        today = datetime.now()
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return self._make_result([])

        with patch("discover_events.subprocess.run", side_effect=fake_run):
            discover_events.fetch_account_events(account, today, calendars=["cal1@example.com", "cal2@example.com"])

        cmd = captured[0]
        assert "--all" not in cmd
        assert "--cal" in cmd
        assert "cal1@example.com" in cmd
        assert "cal2@example.com" in cmd


class TestShouldSkipEvent:
    def _accepted_event(self, extra_attendees=None):
        attendees = [
            {"email": "testuser@work.example.com", "responseStatus": "accepted", "self": True}
        ]
        if extra_attendees:
            attendees.extend(extra_attendees)
        return {
            "summary": "Test Meeting",
            "status": "confirmed",
            "eventType": "default",
            "attendees": attendees,
        }

    def test_keeps_accepted_meeting_with_others(self):
        event = self._accepted_event([{"email": "other@work.example.com", "responseStatus": "accepted"}])
        assert discover_events.should_skip_event(event, USER_EMAILS) is False

    def test_skips_declined(self):
        event = {
            "summary": "Declined",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "declined", "self": True},
                {"email": "other@work.example.com", "responseStatus": "accepted"},
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_skips_working_location(self):
        event = {"eventType": "workingLocation", "attendees": []}
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_skips_cancelled(self):
        event = {
            "status": "cancelled",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "accepted", "self": True},
                {"email": "other@work.example.com", "responseStatus": "accepted"},
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_skips_no_attendees(self):
        event = {"summary": "Solo", "status": "confirmed", "eventType": "default", "attendees": []}
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_skips_self_only(self):
        event = {
            "summary": "Block",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "accepted", "self": True}
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_skips_broadcast(self):
        event = {
            "summary": "Announcement",
            "status": "confirmed",
            "eventType": "default",
            "guestsCanSeeOtherGuests": False,
            "guestsCanInviteOthers": False,
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "needsAction", "self": True}
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_keeps_self_only_with_conference_data(self):
        event = {
            "summary": "Interview with Someone",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "accepted", "self": True}
            ],
            "conferenceData": {
                "conferenceSolution": {"key": {"type": "hangoutsMeet"}},
                "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}],
            },
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is False

    def test_keeps_self_only_with_hangout_link(self):
        event = {
            "summary": "Interview with Someone",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "accepted", "self": True}
            ],
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is False

    def test_keeps_no_attendees_with_meeting_link(self):
        event = {
            "summary": "External Interview",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [],
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is False

    def test_still_skips_self_only_without_meeting_link(self):
        event = {
            "summary": "Focus Time",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "accepted", "self": True}
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is True

    def test_keeps_tentative(self):
        event = {
            "summary": "Maybe",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "tentative", "self": True},
                {"email": "other@work.example.com", "responseStatus": "accepted"},
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is False


class TestMergeEvents:
    def test_deduplicates_by_ical_uid(self):
        events1 = [{"iCalUID": "uid1@google.com", "summary": "Meeting A"}]
        events2 = [{"iCalUID": "uid1@google.com", "summary": "Meeting A (dup)"}]
        merged = discover_events.merge_events([events1, events2])
        assert len(merged) == 1
        assert merged[0]["summary"] == "Meeting A"

    def test_merges_unique_events(self):
        events1 = [{"iCalUID": "uid1@google.com", "summary": "Meeting A"}]
        events2 = [{"iCalUID": "uid2@google.com", "summary": "Meeting B"}]
        merged = discover_events.merge_events([events1, events2])
        assert len(merged) == 2

    def test_handles_empty_lists(self):
        assert discover_events.merge_events([]) == []
        assert discover_events.merge_events([[], []]) == []


class TestFilterEvents:
    def test_filters_fixture_events(self, sample_events):
        events = sample_events["events"]
        filtered = discover_events.filter_events(events, USER_EMAILS)
        summaries = {e["summary"] for e in filtered}
        assert "Test User & Alice Tester 1:1" in summaries
        assert "Team Standup" in summaries
        assert "Declined Meeting" not in summaries
        assert "Working Location" not in summaries
        assert "Broadcast Announcement" not in summaries
        assert "Cancelled Meeting" not in summaries

    def test_returns_only_accepted_meetings(self, sample_events):
        events = sample_events["events"]
        filtered = discover_events.filter_events(events, USER_EMAILS)
        assert len(filtered) == 2
