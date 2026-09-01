"""Tests for associate-artifact-search scripts.

All tests use mocked external dependencies (gh, glab, gog CLIs; Jira API).
"""

import json
import sys
import urllib.request
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_SKILL = Path(__file__).parent.parent / "skills" / "associate-artifact-search"
sys.path.insert(0, str(_SKILL))

import search_gh
import search_glab
import search_gog
import search_jira


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_main(module, argv, mock_subprocess_side_effect=None, capsys=None):
    """Call module.main() with patched sys.argv; return captured stdout as parsed JSON."""
    with patch.object(sys, "argv", argv):
        if mock_subprocess_side_effect is not None:
            with patch(f"{module.__name__}.subprocess.run", side_effect=mock_subprocess_side_effect):
                module.main()
        else:
            module.main()
    out = capsys.readouterr().out if capsys else ""
    return json.loads(out) if out.strip() else {}


def _make_completed(stdout_json, returncode=0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = json.dumps(stdout_json)
    r.stderr = ""
    return r


# ===========================================================================
# search_gh.py
# ===========================================================================


class TestGhParseDate:
    def test_iso_format(self):
        assert search_gh.parse_date("2025-01-15") == date(2025, 1, 15)

    def test_slash_format(self):
        assert search_gh.parse_date("2025/01/15") == date(2025, 1, 15)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse date"):
            search_gh.parse_date("15-01-2025")


class TestGhEvents:
    def test_success_returns_list(self):
        events = [{"type": "PushEvent"}]
        with patch("search_gh.subprocess.run", return_value=_make_completed(events)):
            result = search_gh.gh_events("octocat")
        assert result == events

    def test_nonzero_returncode_returns_none(self):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "Not Found"
        with patch("search_gh.subprocess.run", return_value=r):
            result = search_gh.gh_events("ghost")
        assert result is None

    def test_exception_returns_none(self):
        with patch("search_gh.subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = search_gh.gh_events("anyone")
        assert result is None


class TestGhDateFiltering:
    """Verify only events within [since, until] are processed."""

    def _events_with_dates(self, date_strings):
        return [{"type": "CreateEvent", "created_at": f"{d}T00:00:00Z",
                 "repo": {"name": "org/repo"}, "payload": {"ref_type": "branch", "ref": "feat"}}
                for d in date_strings]

    def test_events_on_boundaries_included(self, capsys):
        events = self._events_with_dates(["2025-01-01", "2025-01-07"])
        argv = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_gh.gh_events", return_value=events):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert result["summary"]["total_events"] == 2

    def test_event_before_since_excluded(self, capsys):
        events = self._events_with_dates(["2024-12-31"])
        argv = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_gh.gh_events", return_value=events):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert result["summary"]["total_events"] == 0

    def test_event_after_until_excluded(self, capsys):
        events = self._events_with_dates(["2025-01-08"])
        argv = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_gh.gh_events", return_value=events):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert result["summary"]["total_events"] == 0


class TestGhEventParsing:
    BASE_ARGV = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-31"]

    def _event(self, etype, payload):
        return {
            "type": etype,
            "created_at": "2025-01-10T09:00:00Z",
            "repo": {"name": "org/myrepo"},
            "payload": payload,
        }

    def test_push_event_strips_refs_heads(self, capsys):
        event = self._event("PushEvent", {
            "ref": "refs/heads/feature-x",
            "commits": [{"sha": "deadbeef1234", "message": "Add feature\n\nLong body"}],
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        push = result["pushes"][0]
        assert push["branch"] == "feature-x"
        assert push["sha"] == "deadbeef"  # first 8 chars
        assert push["message"] == "Add feature"  # first line only

    def test_push_event_message_truncated_to_120(self, capsys):
        long_msg = "A" * 130
        event = self._event("PushEvent", {
            "ref": "refs/heads/main",
            "commits": [{"sha": "abc", "message": long_msg}],
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert len(result["pushes"][0]["message"]) == 120

    def test_pull_request_event_merged_bool(self, capsys):
        event = self._event("PullRequestEvent", {
            "action": "closed",
            "pull_request": {"number": 42, "title": "My PR", "merged": True, "html_url": "https://..."},
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        pr = result["pull_requests"][0]
        assert pr["merged"] is True
        assert pr["action"] == "closed"
        assert pr["number"] == 42

    def test_pull_request_not_merged(self, capsys):
        event = self._event("PullRequestEvent", {
            "action": "opened",
            "pull_request": {"number": 5, "title": "Draft", "merged": False, "html_url": ""},
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["pull_requests"][0]["merged"] is False

    def test_pr_review_event_state_extracted(self, capsys):
        event = self._event("PullRequestReviewEvent", {
            "review": {"state": "approved"},
            "pull_request": {"number": 7, "title": "Review me", "html_url": "https://..."},
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["pr_reviews"][0]["state"] == "approved"

    def test_issues_event(self, capsys):
        event = self._event("IssuesEvent", {
            "action": "closed",
            "issue": {"number": 99, "title": "Bug report", "html_url": "https://..."},
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["issues"][0]["action"] == "closed"
        assert result["issues"][0]["number"] == 99

    def test_issue_comment_event(self, capsys):
        event = self._event("IssueCommentEvent", {
            "issue": {"number": 12, "title": "Some issue", "html_url": "https://..."},
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["issue_comments"][0]["number"] == 12

    def test_release_event(self, capsys):
        event = self._event("ReleaseEvent", {
            "release": {"tag_name": "v1.2.3", "name": "Release 1.2.3", "html_url": "https://..."},
        })
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["releases"][0]["tag"] == "v1.2.3"

    def test_create_event(self, capsys):
        event = self._event("CreateEvent", {"ref_type": "branch", "ref": "feature-y"})
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["creates"][0]["ref_type"] == "branch"
        assert result["creates"][0]["ref"] == "feature-y"

    def test_unknown_event_type_ignored(self, capsys):
        event = self._event("WatchEvent", {"action": "started"})
        with patch("search_gh.gh_events", return_value=[event]):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        # total_events counts the event, but it doesn't appear in any typed list
        assert result["summary"]["total_events"] == 1
        assert result["pushes"] == []
        assert result["pull_requests"] == []


class TestGhSummaryStats:
    BASE_ARGV = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-31"]

    def _pr_event(self, action, merged, repo="org/repo"):
        return {
            "type": "PullRequestEvent",
            "created_at": "2025-01-10T00:00:00Z",
            "repo": {"name": repo},
            "payload": {
                "action": action,
                "pull_request": {"number": 1, "title": "T", "merged": merged, "html_url": ""},
            },
        }

    def test_pull_requests_opened_count(self, capsys):
        events = [self._pr_event("opened", False), self._pr_event("closed", False)]
        with patch("search_gh.gh_events", return_value=events):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["summary"]["pull_requests_opened"] == 1

    def test_pull_requests_merged_count(self, capsys):
        events = [self._pr_event("closed", True), self._pr_event("closed", False)]
        with patch("search_gh.gh_events", return_value=events):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["summary"]["pull_requests_merged"] == 1

    def test_repos_touched_sorted_unique(self, capsys):
        events = [
            self._pr_event("opened", False, repo="org/zebra"),
            self._pr_event("opened", False, repo="org/alpha"),
            self._pr_event("opened", False, repo="org/zebra"),
        ]
        with patch("search_gh.gh_events", return_value=events):
            result = _run_main(search_gh, self.BASE_ARGV, capsys=capsys)
        assert result["summary"]["repos_touched"] == ["org/alpha", "org/zebra"]

    def test_associate_metadata_in_output(self, capsys):
        argv = ["search_gh.py", "--github-user", "octocat", "--since", "2025-01-01",
                "--until", "2025-01-31", "--name", "Octo Cat"]
        with patch("search_gh.gh_events", return_value=[]):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert result["associate"]["github_username"] == "octocat"
        assert result["associate"]["name"] == "Octo Cat"


class TestGhErrorPath:
    def test_gh_events_none_exits_nonzero(self, capsys):
        argv = ["search_gh.py", "--github-user", "bad", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_gh.gh_events", return_value=None):
            with pytest.raises(SystemExit) as exc:
                _run_main(search_gh, argv, capsys=capsys)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "error" in json.loads(out)


# ===========================================================================
# search_glab.py
# ===========================================================================


class TestGlabParseDate:
    def test_iso_format(self):
        assert search_glab.parse_date("2025-03-01") == date(2025, 3, 1)

    def test_slash_format(self):
        assert search_glab.parse_date("2025/03/01") == date(2025, 3, 1)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse date"):
            search_glab.parse_date("bad")


class TestGlabEvents:
    def test_success_returns_list(self):
        events = [{"action_name": "pushed"}]
        with patch("search_glab.subprocess.run", return_value=_make_completed(events)):
            result = search_glab.glab_events("alice")
        assert result == events

    def test_nonzero_returncode_returns_none(self):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "error"
        with patch("search_glab.subprocess.run", return_value=r):
            result = search_glab.glab_events("alice")
        assert result is None

    def test_exception_returns_none(self):
        with patch("search_glab.subprocess.run", side_effect=OSError):
            result = search_glab.glab_events("alice")
        assert result is None


class TestGlabEventProcessing:
    BASE_ARGV = ["search_glab.py", "--gitlab-user", "alice", "--since", "2025-01-01", "--until", "2025-01-31"]

    def _event(self, action, extra=None):
        e = {
            "action_name": action,
            "created_at": "2025-01-10T12:00:00.000Z",
            "target_type": "MergeRequest",
            "target_title": "Fix the thing",
            "project_id": 123,
        }
        if extra:
            e.update(extra)
        return e

    def test_events_grouped_by_action(self, capsys):
        events = [
            self._event("pushed"),
            self._event("pushed"),
            self._event("approved"),
        ]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        assert result["summary"]["actions"]["pushed"] == 2
        assert result["summary"]["actions"]["approved"] == 1

    def test_note_body_extracted(self, capsys):
        events = [self._event("commented on", {"note": {"body": "Great work!"}})]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        entry = result["by_action"]["commented on"][0]
        assert entry["comment_body"] == "Great work!"

    def test_note_not_dict_skipped(self, capsys):
        events = [self._event("commented on", {"note": "not a dict"})]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        entry = result["by_action"]["commented on"][0]
        assert "comment_body" not in entry

    def test_push_data_extracted(self, capsys):
        events = [self._event("pushed", {
            "push_data": {"ref": "main", "commit_title": "Deploy v2", "commit_count": 3}
        })]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        push_data = result["by_action"]["pushed"][0]["push_data"]
        assert push_data["branch"] == "main"
        assert push_data["commit_count"] == 3

    def test_target_title_truncated_to_120(self, capsys):
        events = [self._event("pushed", {"target_title": "X" * 200})]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        assert len(result["by_action"]["pushed"][0]["target_title"]) == 120

    def test_last_activity_from_first_event(self, capsys):
        # Full events list (unfiltered) used for last_activity; first event is most recent
        events = [
            {"action_name": "pushed", "created_at": "2024-12-01T00:00:00Z",
             "target_type": None, "target_title": "", "project_id": 1},
        ]
        argv = ["search_glab.py", "--gitlab-user", "alice", "--since", "2025-01-01", "--until", "2025-01-31"]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, argv, capsys=capsys)
        # event is outside the window so recent=[], but last_activity still comes from events[0]
        assert result["summary"]["last_activity_overall"] == "2024-12-01"
        assert result["summary"]["total_events"] == 0

    def test_last_activity_none_when_no_events(self, capsys):
        with patch("search_glab.glab_events", return_value=[]):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        assert result["summary"]["last_activity_overall"] is None

    def test_events_outside_window_excluded(self, capsys):
        events = [
            {"action_name": "pushed", "created_at": "2024-12-31T23:59:59Z",
             "target_type": None, "target_title": "", "project_id": 1},
        ]
        with patch("search_glab.glab_events", return_value=events):
            result = _run_main(search_glab, self.BASE_ARGV, capsys=capsys)
        assert result["summary"]["total_events"] == 0


class TestGlabErrorPath:
    def test_glab_events_none_exits_nonzero(self, capsys):
        argv = ["search_glab.py", "--gitlab-user", "x", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_glab.glab_events", return_value=None):
            with pytest.raises(SystemExit) as exc:
                _run_main(search_glab, argv, capsys=capsys)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "error" in json.loads(out)


# ===========================================================================
# search_gog.py
# ===========================================================================


class TestGogParseDate:
    def test_iso_format(self):
        assert search_gog.parse_date("2025-06-15") == date(2025, 6, 15)

    def test_slash_format(self):
        assert search_gog.parse_date("2025/06/15") == date(2025, 6, 15)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse date"):
            search_gog.parse_date("tomorrow")


class TestRunGog:
    def test_success_returns_dict(self):
        payload = {"files": []}
        with patch("search_gog.subprocess.run", return_value=_make_completed(payload)):
            result = search_gog.run_gog(["drive", "ls"])
        assert result == payload

    def test_includes_account_flag_when_given(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return _make_completed({})

        with patch("search_gog.subprocess.run", side_effect=fake_run):
            search_gog.run_gog(["drive", "ls"], account="me@example.com")

        assert "--account" in captured[0]
        assert "me@example.com" in captured[0]

    def test_no_account_flag_when_none(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return _make_completed({})

        with patch("search_gog.subprocess.run", side_effect=fake_run):
            search_gog.run_gog(["drive", "ls"])

        assert "--account" not in captured[0]

    def test_nonzero_returncode_returns_none(self):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "auth error"
        with patch("search_gog.subprocess.run", return_value=r):
            assert search_gog.run_gog(["drive", "ls"]) is None

    def test_exception_returns_none(self):
        with patch("search_gog.subprocess.run", side_effect=OSError):
            assert search_gog.run_gog(["drive", "ls"]) is None


class TestSearchDrive:
    def _file(self, owner_email=None, modifier_email=None, mime="application/vnd.google-apps.document"):
        return {
            "id": "file1",
            "name": "Doc One",
            "mimeType": mime,
            "modifiedTime": "2025-01-10T12:00:00Z",
            "createdTime": "2025-01-05T08:00:00Z",
            "webViewLink": "https://docs.google.com/...",
            "lastModifyingUser": {"emailAddress": modifier_email or ""},
            "owners": [{"emailAddress": owner_email}] if owner_email else [],
            "sharingUser": None,
        }

    def _mock_page(self, files, next_token=None):
        return {"files": files, "nextPageToken": next_token}

    def test_file_owned_by_associate_included(self):
        associate = "alice@example.com"
        f = self._file(owner_email=associate)
        with patch("search_gog.fetch_drive_page", return_value=self._mock_page([f])):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert len(result["files"]) == 1
        assert result["files"][0]["owned_by_associate"] is True

    def test_file_last_modified_by_associate_included(self):
        associate = "alice@example.com"
        f = self._file(owner_email="other@example.com", modifier_email=associate)
        with patch("search_gog.fetch_drive_page", return_value=self._mock_page([f])):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert len(result["files"]) == 1
        assert result["files"][0]["last_modified_by_associate"] is True

    def test_file_not_touched_by_associate_excluded(self):
        associate = "alice@example.com"
        f = self._file(owner_email="other@example.com", modifier_email="third@example.com")
        with patch("search_gog.fetch_drive_page", return_value=self._mock_page([f])):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert len(result["files"]) == 0

    def test_associate_as_one_of_multiple_owners_included(self):
        associate = "alice@example.com"
        f = self._file(owner_email=associate)
        f["owners"] = [{"emailAddress": "boss@example.com"}, {"emailAddress": associate}]
        with patch("search_gog.fetch_drive_page", return_value=self._mock_page([f])):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert len(result["files"]) == 1

    def test_mime_type_mapped_to_label(self):
        associate = "alice@example.com"
        for mime, label in search_gog.WORKSPACE_MIMES.items():
            f = self._file(owner_email=associate, mime=mime)
            with patch("search_gog.fetch_drive_page", return_value=self._mock_page([f])):
                result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
            assert result["files"][0]["type"] == label

    def test_by_type_summary_counts(self):
        associate = "alice@example.com"
        doc = self._file(owner_email=associate, mime="application/vnd.google-apps.document")
        sheet = self._file(owner_email=associate, mime="application/vnd.google-apps.spreadsheet")
        sheet["id"] = "file2"
        with patch("search_gog.fetch_drive_page", return_value=self._mock_page([doc, sheet])):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert result["summary"]["by_type"]["Doc"] == 1
        assert result["summary"]["by_type"]["Sheet"] == 1

    def test_pagination_fetches_next_page(self):
        associate = "alice@example.com"
        page1 = self._mock_page([self._file(owner_email=associate)], next_token="tok1")
        file2 = self._file(owner_email=associate)
        file2["id"] = "file2"
        page2 = self._mock_page([file2], next_token=None)

        pages = [page1, page2]
        with patch("search_gog.fetch_drive_page", side_effect=pages):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert len(result["files"]) == 2

    def test_none_response_breaks_loop(self):
        with patch("search_gog.fetch_drive_page", return_value=None):
            result = search_gog.search_drive("alice@example.com", date(2025, 1, 1), date(2025, 1, 31), None)
        assert result["files"] == []


class TestSearchGmailQuery:
    def test_returns_threads_list(self):
        resp = {"threads": [{"id": "t1", "subject": "Hello", "from": "a@b.com", "date": "2025-01-10"}]}
        with patch("search_gog.run_gog", return_value=resp):
            threads = search_gog.search_gmail_query("from:alice@example.com", None)
        assert len(threads) == 1
        assert threads[0]["id"] == "t1"

    def test_pagination_continues_until_no_token(self):
        page1 = {"threads": [{"id": "t1", "subject": "A", "from": "", "date": ""}], "nextPageToken": "p2"}
        page2 = {"threads": [{"id": "t2", "subject": "B", "from": "", "date": ""}]}
        with patch("search_gog.run_gog", side_effect=[page1, page2]):
            threads = search_gog.search_gmail_query("query", None)
        assert len(threads) == 2

    def test_none_response_breaks_loop(self):
        with patch("search_gog.run_gog", return_value=None):
            threads = search_gog.search_gmail_query("query", None)
        assert threads == []


class TestSearchGmail:
    def _thread(self, tid):
        return {"id": tid, "subject": "S", "from": "x@y.com", "date": "2025-01-10", "labels": []}

    def test_deduplication_same_thread_both_queries(self):
        shared = self._thread("shared-id")
        with patch("search_gog.search_gmail_query", side_effect=[[shared], [shared]]):
            result = search_gog.search_gmail("alice@example.com", date(2025, 1, 1), date(2025, 1, 31), None)
        assert result["summary"]["unique_threads"] == 1
        assert result["summary"]["sent_by_associate"] == 1
        assert result["summary"]["sent_to_associate"] == 1

    def test_unique_threads_different_ids(self):
        with patch("search_gog.search_gmail_query", side_effect=[[self._thread("a")], [self._thread("b")]]):
            result = search_gog.search_gmail("alice@example.com", date(2025, 1, 1), date(2025, 1, 31), None)
        assert result["summary"]["unique_threads"] == 2

    def test_gmail_date_format_uses_slash(self):
        queries = []

        def capture_query(q, account):
            queries.append(q)
            return []

        with patch("search_gog.search_gmail_query", side_effect=capture_query):
            search_gog.search_gmail("alice@example.com", date(2025, 3, 5), date(2025, 3, 12), None)

        assert "after:2025/03/05" in queries[0]
        assert "before:2025/03/12" in queries[0]


class TestGogSkipFlags:
    def test_skip_drive_omits_drive_key(self, capsys):
        argv = ["search_gog.py", "--email", "alice@example.com",
                "--since", "2025-01-01", "--until", "2025-01-31", "--skip-drive"]
        gmail_data = {"sent_by_associate": [], "sent_to_associate": [],
                      "summary": {"sent_by_associate": 0, "sent_to_associate": 0, "unique_threads": 0}}
        with patch("search_gog.search_gmail", return_value=gmail_data):
            result = _run_main(search_gog, argv, capsys=capsys)
        assert "drive" not in result
        assert "gmail" in result

    def test_skip_gmail_omits_gmail_key(self, capsys):
        argv = ["search_gog.py", "--email", "alice@example.com",
                "--since", "2025-01-01", "--until", "2025-01-31", "--skip-gmail"]
        with patch("search_gog.search_drive", return_value={"files": [], "summary": {}}):
            result = _run_main(search_gog, argv, capsys=capsys)
        assert "gmail" not in result
        assert "drive" in result


# ===========================================================================
# search_jira.py
# ===========================================================================


class TestJiraParseDate:
    def test_iso_format(self):
        assert search_jira.parse_date("2025-11-20") == date(2025, 11, 20)

    def test_slash_format(self):
        assert search_jira.parse_date("2025/11/20") == date(2025, 11, 20)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse date"):
            search_jira.parse_date("20-11-2025")


class TestGetCreds:
    def test_email_extracted_from_config(self, tmp_path):
        cfg = tmp_path / "jira_config.yaml"
        cfg.write_text("email: alice@redhat.com\nother: value\n")
        with patch("search_jira.Path") as mock_path:
            mock_path.return_value.expanduser.return_value = cfg
            # subprocess is imported locally inside get_creds, so patch at module level
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                email, _ = search_jira.get_creds()
        assert email == "alice@redhat.com"

    def test_email_none_when_config_missing(self):
        missing = Path("/nonexistent/jira_config.yaml")
        with patch("search_jira.Path") as mock_path:
            mock_path.return_value.expanduser.return_value = missing
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                email, _ = search_jira.get_creds()
        assert email is None

    def test_token_decoded_from_keychain(self):
        import base64
        raw_token = "my-api-token"
        b64 = base64.b64encode(raw_token.encode()).decode()
        keychain_output = f"go-keyring-base64:{b64}"
        missing = Path("/nonexistent/jira_config.yaml")
        with patch("search_jira.Path") as mock_path:
            mock_path.return_value.expanduser.return_value = missing
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=keychain_output)
                _, token = search_jira.get_creds()
        assert token == raw_token

    def test_token_none_when_security_fails(self):
        missing = Path("/nonexistent/jira_config.yaml")
        with patch("search_jira.Path") as mock_path:
            mock_path.return_value.expanduser.return_value = missing
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                _, token = search_jira.get_creds()
        assert token is None


class TestResolveAccountId:
    def test_identifier_with_colon_returned_as_is(self):
        result = search_jira.resolve_account_id("accountId:abc-123", "e@e.com", "tok")
        assert result == "accountId:abc-123"

    def test_email_resolved_via_api(self):
        api_response = [{"accountId": "resolved-id-xyz"}]
        with patch("search_jira.api", return_value=api_response):
            result = search_jira.resolve_account_id("alice@example.com", "e@e.com", "tok")
        assert result == "resolved-id-xyz"

    def test_empty_api_response_returns_none(self):
        with patch("search_jira.api", return_value=[]):
            result = search_jira.resolve_account_id("alice@example.com", "e@e.com", "tok")
        assert result is None

    def test_non_list_api_response_returns_none(self):
        with patch("search_jira.api", return_value={"error": "not found"}):
            result = search_jira.resolve_account_id("alice@example.com", "e@e.com", "tok")
        assert result is None


class TestJiraApi:
    def _mock_urlopen(self, response_data):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(response_data).encode()
        return mock_resp

    def test_basic_auth_header_correct(self):
        import base64
        captured_req = []

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return self._mock_urlopen({})

        with patch("search_jira.urllib.request.urlopen", side_effect=fake_urlopen):
            search_jira.api("user@ex.com", "mytoken", "/rest/api/3/search/jql")

        auth = captured_req[0].get_header("Authorization")
        expected = "Basic " + base64.b64encode(b"user@ex.com:mytoken").decode()
        assert auth == expected

    def test_params_appended_to_url(self):
        captured_req = []

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return self._mock_urlopen({})

        with patch("search_jira.urllib.request.urlopen", side_effect=fake_urlopen):
            search_jira.api("u@e.com", "tok", "/rest/api/3/user/search", {"query": "alice", "maxResults": "1"})

        url = captured_req[0].full_url
        assert "query=alice" in url
        assert "maxResults=1" in url

    def test_exception_returns_none(self):
        with patch("search_jira.urllib.request.urlopen", side_effect=OSError("network error")):
            result = search_jira.api("u@e.com", "tok", "/rest/api/3/search/jql")
        assert result is None


class TestFmt:
    def test_extracts_all_fields(self):
        issue = {
            "key": "PROJ-123",
            "fields": {
                "issuetype": {"name": "Bug"},
                "status": {"name": "In Progress"},
                "summary": "Something is broken",
                "created": "2025-01-10T08:00:00.000+0000",
                "updated": "2025-01-11T09:00:00.000+0000",
            },
        }
        result = search_jira.fmt(issue)
        assert result == {
            "key": "PROJ-123",
            "type": "Bug",
            "status": "In Progress",
            "summary": "Something is broken",
            "created": "2025-01-10",
            "updated": "2025-01-11",
        }

    def test_missing_nested_dicts_dont_crash(self):
        issue = {"key": "PROJ-1", "fields": {}}
        result = search_jira.fmt(issue)
        assert result["key"] == "PROJ-1"
        assert result["type"] is None
        assert result["status"] is None


class TestJiraSearch:
    def test_returns_issues_from_dict(self):
        issues = [{"key": "X-1"}, {"key": "X-2"}]
        with patch("search_jira.api", return_value={"issues": issues, "total": 2}):
            result = search_jira.search("u@e.com", "tok", "assignee = me")
        assert result == issues

    def test_returns_empty_list_on_non_dict(self):
        with patch("search_jira.api", return_value=None):
            result = search_jira.search("u@e.com", "tok", "assignee = me")
        assert result == []


class TestJiraChangelogFiltering:
    """Test that edited issues correctly filter changelog by author and date."""

    BASE_ARGV = ["search_jira.py", "--jira-id", "accountId:abc", "--since", "2025-01-01", "--until", "2025-01-31"]
    ACCOUNT_ID = "accountId:abc"

    def _issue(self, histories):
        return {
            "key": "PROJ-1",
            "fields": {
                "issuetype": {"name": "Task"},
                "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                "summary": "A task",
                "created": "2025-01-05T00:00:00Z",
                "updated": "2025-01-10T00:00:00Z",
            },
            "changelog": {"histories": histories},
        }

    def _history(self, account_id, created, fields=("summary",)):
        return {
            "author": {"accountId": account_id},
            "created": created,
            "items": [{"field": f} for f in fields],
        }

    def test_history_by_associate_after_since_included(self, capsys):
        history = self._history(self.ACCOUNT_ID, "2025-01-15T10:00:00Z", ("summary", "status"))
        issue = self._issue([history])
        with patch("search_jira.get_creds", return_value=("u@e.com", "tok")):
            with patch("search_jira.resolve_account_id", return_value=self.ACCOUNT_ID):
                with patch("search_jira.search", side_effect=[[], [issue], []]):
                    result = _run_main(search_jira, self.BASE_ARGV, capsys=capsys)
        assert len(result["edited"]) == 1
        assert result["edited"][0]["changes"][0]["fields"] == ["summary", "status"]

    def test_history_before_since_excluded(self, capsys):
        history = self._history(self.ACCOUNT_ID, "2024-12-31T23:59:59Z")
        issue = self._issue([history])
        with patch("search_jira.get_creds", return_value=("u@e.com", "tok")):
            with patch("search_jira.resolve_account_id", return_value=self.ACCOUNT_ID):
                with patch("search_jira.search", side_effect=[[], [issue], []]):
                    result = _run_main(search_jira, self.BASE_ARGV, capsys=capsys)
        assert len(result["edited"]) == 0

    def test_history_by_other_user_excluded(self, capsys):
        history = self._history("other:user-id", "2025-01-15T10:00:00Z")
        issue = self._issue([history])
        with patch("search_jira.get_creds", return_value=("u@e.com", "tok")):
            with patch("search_jira.resolve_account_id", return_value=self.ACCOUNT_ID):
                with patch("search_jira.search", side_effect=[[], [issue], []]):
                    result = _run_main(search_jira, self.BASE_ARGV, capsys=capsys)
        assert len(result["edited"]) == 0


class TestJiraClosedIssues:
    BASE_ARGV = ["search_jira.py", "--jira-id", "accountId:abc", "--since", "2025-01-01", "--until", "2025-01-31"]

    def _issue(self, status_category_key):
        return {
            "key": "PROJ-2",
            "fields": {
                "issuetype": {"name": "Bug"},
                "status": {"name": "Done", "statusCategory": {"key": status_category_key}},
                "summary": "Fixed",
                "created": "2025-01-01T00:00:00Z",
                "updated": "2025-01-20T00:00:00Z",
            },
            "changelog": {"histories": []},
        }

    def test_done_status_category_included_in_closed(self, capsys):
        issue = self._issue("done")
        with patch("search_jira.get_creds", return_value=("u@e.com", "tok")):
            with patch("search_jira.resolve_account_id", return_value="accountId:abc"):
                with patch("search_jira.search", side_effect=[[], [], [issue]]):
                    result = _run_main(search_jira, self.BASE_ARGV, capsys=capsys)
        assert len(result["closed"]) == 1

    def test_other_status_category_excluded_from_closed(self, capsys):
        issue = self._issue("indeterminate")
        with patch("search_jira.get_creds", return_value=("u@e.com", "tok")):
            with patch("search_jira.resolve_account_id", return_value="accountId:abc"):
                with patch("search_jira.search", side_effect=[[], [], [issue]]):
                    result = _run_main(search_jira, self.BASE_ARGV, capsys=capsys)
        assert len(result["closed"]) == 0


class TestJiraErrorPaths:
    def test_missing_creds_exits_nonzero(self, capsys):
        argv = ["search_jira.py", "--jira-id", "accountId:abc",
                "--since", "2025-01-01", "--until", "2025-01-31"]
        with patch("search_jira.get_creds", return_value=(None, None)):
            with pytest.raises(SystemExit) as exc:
                _run_main(search_jira, argv, capsys=capsys)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "error" in json.loads(out)

    def test_unresolvable_account_exits_nonzero(self, capsys):
        argv = ["search_jira.py", "--jira-id", "nobody@example.com",
                "--since", "2025-01-01", "--until", "2025-01-31"]
        with patch("search_jira.get_creds", return_value=("u@e.com", "tok")):
            with patch("search_jira.resolve_account_id", return_value=None):
                with pytest.raises(SystemExit) as exc:
                    _run_main(search_jira, argv, capsys=capsys)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "error" in json.loads(out)
