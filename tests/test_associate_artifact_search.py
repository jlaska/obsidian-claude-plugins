"""Tests for associate-artifact-search scripts.

All tests use mocked external dependencies (gh, glab, gog CLIs; Jira API).
"""

import json
import sys
import urllib.error
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
import search_linear


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


_EMPTY_SEARCH = {"authored_prs": [], "reviewed_prs": [], "commented_prs": [], "commits": []}


@pytest.fixture(autouse=True)
def _stub_gh_search(request):
    """Legacy search_gh tests only mock the events feed; keep `gh search` off the network."""
    if request.cls is not None and request.cls.__name__.startswith("TestGh") \
            and not request.cls.__name__.startswith("TestGhSearch"):
        with patch("search_gh.search_activity", return_value=dict(_EMPTY_SEARCH)):
            yield
    else:
        yield


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
        with patch("search_gh.gh_events", return_value=None), \
             patch("search_gh.search_activity", return_value=None):
            with pytest.raises(SystemExit) as exc:
                _run_main(search_gh, argv, capsys=capsys)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "error" in json.loads(out)

    def test_events_failure_alone_is_a_warning(self, capsys):
        argv = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_gh.gh_events", return_value=None):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert any("events feed unavailable" in w for w in result["warnings"])

    def test_search_failure_alone_is_a_warning(self, capsys):
        argv = ["search_gh.py", "--github-user", "u", "--since", "2025-01-01", "--until", "2025-01-07"]
        with patch("search_gh.gh_events", return_value=[]), \
             patch("search_gh.search_activity", return_value=None):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert any("gh search unavailable" in w for w in result["warnings"])


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

    def test_file_not_owned_by_associate_flags_correctly(self):
        associate = "alice@example.com"
        f = self._file(owner_email="other@example.com", modifier_email="third@example.com")
        with patch("search_gog.fetch_drive_page", return_value=self._mock_page([f])):
            result = search_gog.search_drive(associate, date(2025, 1, 1), date(2025, 1, 31), None)
        assert len(result["files"]) == 1
        assert result["files"][0]["owned_by_associate"] is False
        assert result["files"][0]["last_modified_by_associate"] is False

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

    def test_gmail_until_date_is_inclusive_and_uses_slash(self):
        queries = []

        def capture_query(q, account):
            queries.append(q)
            return []

        with patch("search_gog.search_gmail_query", side_effect=capture_query):
            search_gog.search_gmail("alice@example.com", date(2025, 3, 5), date(2025, 3, 12), None)

        assert "after:2025/03/05" in queries[0]
        assert "before:2025/03/13" in queries[0]


class TestGogSkipFlags:
    def test_skip_drive_omits_drive_key(self, capsys):
        argv = ["search_gog.py", "--email", "alice@example.com",
                "--since", "2025-01-01", "--until", "2025-01-31", "--skip-drive"]
        gmail_data = {"sent_by_associate": [], "sent_to_associate": [],
                      "summary": {"sent_by_associate": 0, "sent_to_associate": 0, "unique_threads": 0}}
        with patch("search_gog.search_gmail", return_value=gmail_data), \
             patch("search_gog.resolve_display_name", return_value="alice"):
            result = _run_main(search_gog, argv, capsys=capsys)
        assert "drive" not in result
        assert "gmail" in result

    def test_skip_gmail_omits_gmail_key(self, capsys):
        argv = ["search_gog.py", "--email", "alice@example.com",
                "--since", "2025-01-01", "--until", "2025-01-31", "--skip-gmail"]
        with patch("search_gog.search_drive", return_value={"files": [], "summary": {}}), \
             patch("search_gog.resolve_display_name", return_value="alice"):
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


# ===========================================================================
# search_linear.py
# ===========================================================================

_UID = "74816ec0-5514-4584-9eff-5f8e22a99662"


def _linear_resp(data=None, errors=None):
    """Mock urlopen context manager returning a GraphQL payload."""
    payload = {}
    if data is not None:
        payload["data"] = data
    if errors is not None:
        payload["errors"] = errors
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _issue(identifier="ENG-1", **over):
    base = {
        "identifier": identifier, "title": "Do thing", "url": f"https://linear.app/x/issue/{identifier}",
        "branchName": f"me/{identifier.lower()}-do-thing",
        "createdAt": "2026-11-10T12:00:00.000Z", "updatedAt": "2026-11-11T12:00:00.000Z",
        "completedAt": None, "state": {"name": "In Progress", "type": "started"},
        "team": {"key": "ENG"}, "project": {"name": "Proj"}, "cycle": {"number": 3},
        "labels": {"nodes": [{"name": "bug"}]},
        "attachments": {"nodes": [{"url": "https://github.com/o/r/pull/9", "sourceType": "github", "title": "PR"}]},
    }
    base.update(over)
    return base


class TestLinearParseDate:
    def test_iso(self):
        assert search_linear.parse_date("2026-11-17") == date(2026, 11, 17)

    def test_invalid(self):
        with pytest.raises(ValueError):
            search_linear.parse_date("nope")


class TestLinearGraphql:
    def test_returns_data(self):
        with patch("search_linear.urllib.request.urlopen", return_value=_linear_resp({"viewer": {"id": "1"}})):
            assert search_linear.graphql("k", "q") == {"viewer": {"id": "1"}}

    def test_sends_raw_key_without_bearer(self):
        with patch("search_linear.urllib.request.urlopen", return_value=_linear_resp({})) as uo:
            search_linear.graphql("lin_api_x", "q")
        assert uo.call_args[0][0].get_header("Authorization") == "lin_api_x"

    def test_graphql_errors_raise(self):
        with patch("search_linear.urllib.request.urlopen",
                   return_value=_linear_resp(errors=[{"message": "bad filter"}])):
            with pytest.raises(search_linear.LinearError, match="bad filter"):
                search_linear.graphql("k", "q")

    def test_network_error_raises(self):
        with patch("search_linear.urllib.request.urlopen", side_effect=OSError("down")):
            with pytest.raises(search_linear.LinearError):
                search_linear.graphql("k", "q")


class TestLinearPaginate:
    def test_follows_cursor(self):
        pages = [
            _linear_resp({"issues": {"nodes": [{"a": 1}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}),
            _linear_resp({"issues": {"nodes": [{"a": 2}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}),
        ]
        with patch("search_linear.urllib.request.urlopen", side_effect=pages):
            nodes = search_linear.paginate("k", "q", {}, "issues")
        assert nodes == [{"a": 1}, {"a": 2}]

    def test_caps_pages(self):
        forever = _linear_resp({"issues": {"nodes": [{"a": 1}], "pageInfo": {"hasNextPage": True, "endCursor": "c"}}})
        with patch("search_linear.urllib.request.urlopen", return_value=forever):
            nodes = search_linear.paginate("k", "q", {}, "issues")
        assert len(nodes) == search_linear.MAX_PAGES


class TestLinearResolveUser:
    def test_email_lookup(self):
        user = {"id": _UID, "name": "J", "displayName": "j", "email": "j@x.com"}
        with patch("search_linear.urllib.request.urlopen",
                   return_value=_linear_resp({"users": {"nodes": [user]}})) as uo:
            assert search_linear.resolve_user("j@x.com", "k") == user
        sent = json.loads(uo.call_args[0][0].data)
        assert sent["variables"]["filter"] == {"email": {"eq": "j@x.com"}}

    def test_uuid_lookup(self):
        user = {"id": _UID, "name": "J", "displayName": "j", "email": "j@x.com"}
        with patch("search_linear.urllib.request.urlopen", return_value=_linear_resp({"user": user})):
            assert search_linear.resolve_user(_UID, "k") == user

    def test_name_lookup_uses_or_filter(self):
        with patch("search_linear.urllib.request.urlopen",
                   return_value=_linear_resp({"users": {"nodes": []}})) as uo:
            assert search_linear.resolve_user("Jane Doe", "k") is None
        sent = json.loads(uo.call_args[0][0].data)
        assert "or" in sent["variables"]["filter"]


class TestLinearFormatting:
    def test_window_inclusive_bounds(self):
        w = search_linear.window(date(2026, 11, 1), date(2026, 11, 7))
        assert w == {"gte": "2026-11-01T00:00:00.000Z", "lte": "2026-11-07T23:59:59.999Z"}

    def test_fmt_issue(self):
        out = search_linear.fmt_issue(_issue())
        assert out["identifier"] == "ENG-1"
        assert out["status_type"] == "started"
        assert out["created"] == "2026-11-10"
        assert out["completed"] is None
        assert out["labels"] == ["bug"]
        assert out["linked"][0]["source"] == "github"

    def test_fmt_issue_handles_nulls(self):
        out = search_linear.fmt_issue({"identifier": "ENG-2", "state": None, "project": None,
                                       "cycle": None, "labels": None, "attachments": None})
        assert out["project"] is None and out["labels"] == [] and out["linked"] == []

    def test_fmt_comment_truncates(self):
        c = {"createdAt": "2026-11-10T00:00:00Z", "body": "x" * 1000, "issue": {"identifier": "ENG-1"}}
        out = search_linear.fmt_comment(c)
        assert out["body"].endswith("…")
        assert len(out["body"]) == search_linear.COMMENT_TRUNCATE + 1


class TestLinearStatusChanges:
    def _hist(self, actor, day, to="Done", to_type="completed"):
        return {"createdAt": f"{day}T10:00:00.000Z", "actor": {"id": actor},
                "fromState": {"name": "In Progress"}, "toState": {"name": to, "type": to_type}}

    def test_only_user_transitions_in_window(self):
        issue = _issue(history={"nodes": [
            self._hist(_UID, "2026-11-11"),                      # keep
            self._hist("someone-else", "2026-11-11"),            # wrong actor
            self._hist(_UID, "2026-10-01"),                      # before window
            {"createdAt": "2026-11-11T10:00:00Z", "actor": {"id": _UID}, "toState": None},  # not a state change
        ]})
        out = search_linear.status_changes_by([issue], _UID, "2026-11-01", "2026-11-17")
        assert len(out) == 1
        assert out[0]["transitions"] == [
            {"date": "2026-11-11", "from": "In Progress", "to": "Done", "to_type": "completed"}]

    def test_no_transitions_omitted(self):
        assert search_linear.status_changes_by([_issue(history={"nodes": []})], _UID, "a", "b") == []


class TestLinearMain:
    ARGV = ["search_linear.py", "--linear-user", "j@x.com", "--since", "2026-11-01", "--until", "2026-11-17"]

    def test_missing_api_key(self, capsys, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        with patch.object(sys, "argv", self.ARGV), pytest.raises(SystemExit) as e:
            search_linear.main()
        assert e.value.code == 1
        assert "LINEAR_API_KEY" in json.loads(capsys.readouterr().out)["error"]

    def test_unresolved_user(self, capsys, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "k")
        with patch.object(sys, "argv", self.ARGV), \
             patch("search_linear.resolve_user", return_value=None), pytest.raises(SystemExit):
            search_linear.main()
        assert "Could not resolve" in json.loads(capsys.readouterr().out)["error"]

    def test_api_error_reported_as_json(self, capsys, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "k")
        with patch.object(sys, "argv", self.ARGV), \
             patch("search_linear.resolve_user", side_effect=search_linear.LinearError("boom")), \
             pytest.raises(SystemExit):
            search_linear.main()
        assert json.loads(capsys.readouterr().out)["error"] == "boom"

    def test_full_run(self, capsys, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "k")
        user = {"id": _UID, "name": "James", "displayName": "j", "email": "j@x.com"}
        issue_hist = _issue("ENG-5", history={"nodes": [{
            "createdAt": "2026-11-11T10:00:00Z", "actor": {"id": _UID},
            "fromState": {"name": "Todo"}, "toState": {"name": "Done", "type": "completed"}}]})
        comment = {"createdAt": "2026-11-12T00:00:00Z", "body": "hi", "url": "u",
                   "issue": {"identifier": "ENG-5", "title": "t"}}

        def fake_paginate(key, query, variables, connection):
            if connection == "comments":
                return [comment]
            if query is search_linear.ISSUES_WITH_HISTORY_QUERY:
                return [issue_hist]
            return [_issue("ENG-1")]

        with patch.object(sys, "argv", self.ARGV), \
             patch("search_linear.resolve_user", return_value=user), \
             patch("search_linear.paginate", side_effect=fake_paginate):
            search_linear.main()
        out = json.loads(capsys.readouterr().out)
        assert out["associate"]["linear_user_id"] == _UID
        assert out["summary"] == {"created": 1, "completed": 1, "updated": 1,
                                  "status_changes": 1, "comments": 1}
        assert out["timeframe"] == {"since": "2026-11-01", "until": "2026-11-17"}


class TestLinearCommentContext:
    def test_issue_comment(self):
        out = search_linear.fmt_comment({"createdAt": "2026-11-01T00:00:00Z", "body": "x",
                                         "issue": {"identifier": "ENG-1", "title": "T", "url": "u"}})
        assert out["context"] == "issue" and out["issue"] == "ENG-1"

    def test_inline_description_comment_uses_document_content_issue(self):
        out = search_linear.fmt_comment({"createdAt": "2026-11-01T00:00:00Z", "body": "x", "issue": None,
                                         "documentContent": {"issue": {"identifier": "ENG-9", "title": "T"}}})
        assert out["context"] == "issue" and out["issue"] == "ENG-9"

    def test_document_comment(self):
        out = search_linear.fmt_comment({"createdAt": "2026-11-01T00:00:00Z", "body": "x", "issue": None,
                                         "documentContent": {"issue": None, "document": {"title": "PRD", "url": "d"}}})
        assert (out["context"], out["context_title"], out["issue"]) == ("document", "PRD", None)

    def test_project_update_comment(self):
        out = search_linear.fmt_comment({"createdAt": "2026-11-01T00:00:00Z", "body": "x",
                                         "projectUpdate": {"url": "pu", "project": {"name": "Proj"}}})
        assert (out["context"], out["context_title"]) == ("project_update", "Proj")

    def test_unknown_parent(self):
        assert search_linear.fmt_comment({"createdAt": "2026-11-01T00:00:00Z", "body": "x"})["context"] is None


class TestLinearHttpError:
    def test_http_400_surfaces_graphql_message(self):
        body = json.dumps({"errors": [{"message": "Field \"nope\" not found"}]}).encode()
        err = urllib.error.HTTPError("u", 400, "Bad Request", {}, BytesIO(body))
        with patch("search_linear.urllib.request.urlopen", side_effect=err):
            with pytest.raises(search_linear.LinearError, match="nope"):
                search_linear.graphql("k", "q")

    def test_http_error_without_json_body(self):
        err = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, BytesIO(b"<html>"))
        with patch("search_linear.urllib.request.urlopen", side_effect=err):
            with pytest.raises(search_linear.LinearError, match="request failed"):
                search_linear.graphql("k", "q")


# ---------------------------------------------------------------------------
# search_gh.py — search-based collection (covers private/internal repos)
# ---------------------------------------------------------------------------

def _spr(number, repo="org/repo", author="u", state="merged", created="2026-11-05T00:00:00Z",
         updated="2026-11-06T00:00:00Z"):
    return {"number": number, "title": f"PR {number}", "repository": {"nameWithOwner": repo},
            "author": {"login": author}, "state": state, "createdAt": created,
            "updatedAt": updated, "closedAt": updated, "url": f"https://x/{number}", "isDraft": False}


class TestGhSearch:
    def test_success(self):
        with patch("search_gh.subprocess.run", return_value=_make_completed([{"number": 1}])):
            assert search_gh.gh_search("prs", ["--author", "u"], "number") == [{"number": 1}]

    def test_command_shape(self):
        with patch("search_gh.subprocess.run", return_value=_make_completed([])) as run:
            search_gh.gh_search("commits", ["--author", "u"], "sha")
        cmd = run.call_args[0][0]
        assert cmd[:3] == ["gh", "search", "commits"] and "--json" in cmd and "--limit" in cmd

    def test_failure_returns_none(self):
        r = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("search_gh.subprocess.run", return_value=r):
            assert search_gh.gh_search("prs", [], "number") is None

    def test_exception_returns_none(self):
        with patch("search_gh.subprocess.run", side_effect=FileNotFoundError):
            assert search_gh.gh_search("prs", [], "number") is None


class TestGhSearchActivity:
    def _run(self, by_kind, activity=None, user="u"):
        """by_kind maps the first distinguishing flag -> rows; activity maps number -> gh pr view payload."""
        def fake_search(kind, flags, fields):
            if kind == "commits":
                return by_kind.get("commits", [])
            for flag in ("--created", "--merged-at", "--reviewed-by", "--commenter"):
                if flag in flags:
                    return by_kind.get(flag)
            raise AssertionError(flags)

        def fake_activity(repo, number):
            return (activity or {}).get(number, {"reviews": [], "comments": []})

        warnings = []
        with patch("search_gh.gh_search", side_effect=fake_search), \
             patch("search_gh.gh_pr_activity", side_effect=fake_activity):
            out = search_gh.search_activity(user, "2026-11-01", "2026-11-17", warnings)
        return out, warnings

    def test_authored_union_dedupes_and_flags_merge(self):
        out, _ = self._run({"--created": [_spr(1), _spr(2)], "--merged-at": [_spr(2), _spr(3)]})
        by_num = {e["number"]: e for e in out["authored_prs"]}
        assert set(by_num) == {1, 2, 3}
        assert by_num[1]["merged_in_window"] is False
        assert by_num[2]["created_in_window"] and by_num[2]["merged_in_window"]
        assert by_num[3]["created_in_window"] is False and by_num[3]["merged_in_window"] is True

    def test_reviews_filtered_to_user_and_window(self):
        activity = {10: {"reviews": [
            {"author": {"login": "u"}, "submittedAt": "2026-11-05T00:00:00Z", "state": "APPROVED"},
            {"author": {"login": "other"}, "submittedAt": "2026-11-05T00:00:00Z", "state": "APPROVED"},
            {"author": {"login": "U"}, "submittedAt": "2026-10-01T00:00:00Z", "state": "COMMENTED"},
        ], "comments": []}}
        out, _ = self._run({"--reviewed-by": [_spr(10, author="peer")], "--commenter": []}, activity)
        assert len(out["reviewed_prs"]) == 1
        assert out["reviewed_prs"][0]["reviews"] == [{"date": "2026-11-05", "state": "APPROVED"}]
        assert out["commented_prs"] == []

    def test_updated_by_others_only_is_dropped(self):
        activity = {11: {"reviews": [{"author": {"login": "other"}, "submittedAt": "2026-11-05T00:00:00Z",
                                      "state": "APPROVED"}],
                         "comments": [{"author": {"login": "other"}, "createdAt": "2026-11-05T00:00:00Z"}]}}
        out, _ = self._run({"--reviewed-by": [_spr(11, author="peer")],
                            "--commenter": [_spr(11, author="peer")]}, activity)
        assert out["reviewed_prs"] == [] and out["commented_prs"] == []

    def test_own_prs_excluded_from_reviewed(self):
        out, _ = self._run({"--reviewed-by": [_spr(12, author="u")], "--commenter": []},
                           {12: {"reviews": [{"author": {"login": "u"},
                                              "submittedAt": "2026-11-05T00:00:00Z", "state": "COMMENTED"}]}})
        assert out["reviewed_prs"] == []

    def test_comments_collected(self):
        activity = {13: {"reviews": [], "comments": [
            {"author": {"login": "u"}, "createdAt": "2026-11-05T00:00:00Z", "url": "c"}]}}
        out, _ = self._run({"--reviewed-by": [], "--commenter": [_spr(13, author="peer")]}, activity)
        assert out["commented_prs"][0]["comments"] == [{"date": "2026-11-05", "url": "c"}]

    def test_commits_parsed(self):
        commits = [{"repository": {"fullName": "org/r"}, "sha": "abcdef1234567890",
                    "commit": {"message": "fix: thing\n\nbody", "committer": {"date": "2026-11-02T10:00:00Z"}},
                    "url": "u"}]
        out, _ = self._run({"commits": commits})
        assert out["commits"] == [{"repo": "org/r", "sha": "abcdef12", "date": "2026-11-02",
                                   "message": "fix: thing", "url": "u"}]

    def test_partial_failure_warns(self):
        out, warnings = self._run({"--created": [_spr(1)], "--merged-at": None})
        assert out is not None and any("merged" in w for w in warnings)

    def test_all_failures_return_none(self):
        with patch("search_gh.gh_search", return_value=None):
            assert search_gh.search_activity("u", "2026-11-01", "2026-11-17", []) is None

    def test_unverifiable_pr_warns_and_is_omitted(self):
        def fake_search(kind, flags, fields):
            return [_spr(14, author="peer")] if "--reviewed-by" in flags else []
        warnings = []
        with patch("search_gh.gh_search", side_effect=fake_search), \
             patch("search_gh.gh_pr_activity", return_value=None):
            out = search_gh.search_activity("u", "2026-11-01", "2026-11-17", warnings)
        assert out["reviewed_prs"] == [] and any("could not verify" in w for w in warnings)

    def test_candidate_cap_warns(self):
        rows = [_spr(n, author="peer", updated=f"2026-11-{(n % 28) + 1:02d}T00:00:00Z") for n in range(150)]
        def fake_search(kind, flags, fields):
            return rows if "--reviewed-by" in flags else []
        warnings = []
        with patch("search_gh.gh_search", side_effect=fake_search), \
             patch("search_gh.gh_pr_activity", return_value={"reviews": [], "comments": []}) as act:
            search_gh.search_activity("u", "2026-11-01", "2026-11-17", warnings)
        assert act.call_count == search_gh.MAX_VERIFY
        assert any("only the" in w for w in warnings)


class TestGhPrActivity:
    def test_success(self):
        with patch("search_gh.subprocess.run", return_value=_make_completed({"reviews": [], "comments": []})):
            assert search_gh.gh_pr_activity("o/r", 1) == {"reviews": [], "comments": []}

    def test_failure(self):
        with patch("search_gh.subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="x")):
            assert search_gh.gh_pr_activity("o/r", 1) is None


class TestGhMainWithSearch:
    def test_merges_search_into_output_and_summary(self, capsys):
        searched = {
            "authored_prs": [{"repo": "org/priv", "number": 1, "merged_in_window": True, "created": "2026-11-02"}],
            "reviewed_prs": [{"repo": "org/other", "number": 2}],
            "commented_prs": [],
            "commits": [{"repo": "org/priv", "sha": "aaaaaaaa", "date": "2026-11-02", "message": "m", "url": "u"}],
        }
        events = [{"type": "PushEvent", "created_at": "2026-11-02T00:00:00Z", "repo": {"name": "org/pub"},
                   "payload": {"ref": "refs/heads/m", "commits": [
                       {"sha": "aaaaaaaa11", "message": "m"}, {"sha": "bbbbbbbb11", "message": "n"}]}}]
        argv = ["search_gh.py", "--github-user", "u", "--since", "2026-11-01", "--until", "2026-11-17"]
        with patch("search_gh.gh_events", return_value=events), \
             patch("search_gh.search_activity", return_value=searched):
            result = _run_main(search_gh, argv, capsys=capsys)
        assert result["summary"]["commits"] == 2  # aaaaaaaa de-duplicated across both sources
        assert result["summary"]["authored_prs"] == 1
        assert result["summary"]["authored_prs_merged_in_window"] == 1
        assert result["summary"]["reviewed_prs"] == 1
        assert result["summary"]["repos_touched"] == ["org/other", "org/priv", "org/pub"]
        assert result["warnings"] == []
