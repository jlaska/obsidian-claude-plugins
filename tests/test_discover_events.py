"""Tests for discover_events.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import discover_events


USER_EMAILS = {"testuser@work.example.com", "testuser@personal.example.com"}


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

    def test_skips_tentative(self):
        event = {
            "summary": "Maybe",
            "status": "confirmed",
            "eventType": "default",
            "attendees": [
                {"email": "testuser@work.example.com", "responseStatus": "tentative", "self": True},
                {"email": "other@work.example.com", "responseStatus": "accepted"},
            ],
        }
        assert discover_events.should_skip_event(event, USER_EMAILS) is True


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
