"""Tests for discover_self.py."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import discover_self


class TestDiscoverAccounts:
    def test_parses_account_list(self, gog_auth_list_text):
        with patch("discover_self._run", return_value=gog_auth_list_text):
            accounts = discover_self._discover_gog_accounts()
        assert len(accounts) == 2
        emails = {a["email"] for a in accounts}
        assert "testuser@work.example.com" in emails
        assert "testuser@personal.example.com" in emails

    def test_returns_empty_when_gog_unavailable(self):
        with patch("discover_self._run", return_value=None):
            accounts = discover_self._discover_gog_accounts()
        assert accounts == []


class TestExtractName:
    def test_extracts_primary_name(self, gog_whoami_json):
        person = json.loads(gog_whoami_json)["person"]
        name = discover_self._extract_name(person)
        assert name == "Test User"

    def test_returns_none_for_empty(self):
        assert discover_self._extract_name({}) is None


class TestExtractEmails:
    def test_extracts_emails(self, gog_whoami_json):
        person = json.loads(gog_whoami_json)["person"]
        emails = discover_self._extract_emails(person)
        assert "testuser@work.example.com" in emails
        assert "testuser@personal.example.com" in emails

    def test_returns_empty_set_for_empty(self):
        assert discover_self._extract_emails({}) == set()


class TestDiscoverSelf:
    def test_returns_identity_dict(self, gog_whoami_json, gog_auth_list_text):
        whoami_person = json.loads(gog_whoami_json)["person"]

        with patch("discover_self._discover_gog_accounts", return_value=[
            {"email": "testuser@work.example.com", "client": "default"},
        ]):
            with patch("discover_self._whoami_for_account", return_value=whoami_person):
                identity = discover_self.discover_self()

        assert "username" in identity
        assert "emails" in identity
        assert "display_name" in identity
        assert "first_name" in identity
        assert "testuser@work.example.com" in identity["emails"]
        assert identity["display_name"] == "Test User"
        assert identity["first_name"] == "Test"

    def test_handles_no_gog_accounts(self):
        with patch("discover_self._discover_gog_accounts", return_value=[]):
            identity = discover_self.discover_self()
        assert isinstance(identity["emails"], list)
        assert "username" in identity

    def test_json_serializable(self, gog_whoami_json, gog_auth_list_text):
        whoami_person = json.loads(gog_whoami_json)["person"]
        with patch("discover_self._discover_gog_accounts", return_value=[
            {"email": "testuser@work.example.com", "client": "default"},
        ]):
            with patch("discover_self._whoami_for_account", return_value=whoami_person):
                identity = discover_self.discover_self()
        json_str = json.dumps(identity)
        parsed = json.loads(json_str)
        assert parsed["first_name"] == "Test"
