"""Tests for Gemini Notes tab fetching and processing functions in sync_to_vault.py."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import sync_to_vault as stv


# ---------------------------------------------------------------------------
# Fixtures: minimal Google Docs API tab JSON
# ---------------------------------------------------------------------------

def _make_tab_doc(tab_title: str, paragraphs: list) -> dict:
    """Build a minimal Google Docs API response with one tab containing given paragraphs."""
    content = []
    for style, text, bold in paragraphs:
        tr = {"content": text}
        if bold:
            tr["textStyle"] = {"bold": True}
        content.append({
            "paragraph": {
                "paragraphStyle": {"namedStyleType": style},
                "elements": [{"textRun": tr}],
            }
        })
    return {
        "tabs": [{
            "tabProperties": {"title": tab_title, "tabId": "t.abc123"},
            "documentTab": {"body": {"content": content}},
        }]
    }


NOTES_DOC = _make_tab_doc("Notes", [
    ("HEADING_2", "Randy / James", False),
    ("NORMAL_TEXT", "Invited  ", False),
    ("NORMAL_TEXT", "Attachments", False),
    ("HEADING_3", "Summary", False),
    ("NORMAL_TEXT", "Strategic alignment discussion.", False),
    ("HEADING_3", "Decisions", False),
    ("NORMAL_TEXT", "Decision one was made.", True),
    ("HEADING_3", "Next steps", False),
    ("NORMAL_TEXT", "[Randy] Complete the report.", False),
    ("HEADING_3", "Details", False),
    ("NORMAL_TEXT", "Detailed discussion notes here.", False),
    ("NORMAL_TEXT", "You should review Gemini's notes to make sure they're accurate.", False),
    ("NORMAL_TEXT", "How is the quality of these specific notes? Take a short survey.", False),
])


# ---------------------------------------------------------------------------
# _body_content_to_markdown
# ---------------------------------------------------------------------------

class TestBodyContentToMarkdown:

    def test_heading_levels(self):
        body = [
            {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_1"}, "elements": [{"textRun": {"content": "H1"}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_2"}, "elements": [{"textRun": {"content": "H2"}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_3"}, "elements": [{"textRun": {"content": "H3"}}]}},
        ]
        result = stv._body_content_to_markdown(body)
        assert "# H1" in result
        assert "## H2" in result
        assert "### H3" in result

    def test_bold_text(self):
        body = [{"paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [{"textRun": {"content": "bold word", "textStyle": {"bold": True}}}],
        }}]
        result = stv._body_content_to_markdown(body)
        assert "**bold word**" in result

    def test_skips_empty_paragraphs(self):
        body = [
            {"paragraph": {"paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "elements": [{"textRun": {"content": "   "}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "elements": [{"textRun": {"content": "real"}}]}},
        ]
        result = stv._body_content_to_markdown(body)
        assert result == "real"

    def test_soft_return_becomes_double_newline(self):
        body = [{"paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [{"textRun": {"content": "line one\x0bline two"}}],
        }}]
        result = stv._body_content_to_markdown(body)
        assert "line one\n\nline two" in result

    def test_skips_non_paragraph_elements(self):
        body = [
            {"sectionBreak": {}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "elements": [{"textRun": {"content": "text"}}]}},
        ]
        result = stv._body_content_to_markdown(body)
        assert result == "text"


# ---------------------------------------------------------------------------
# _strip_gemini_notes_content
# ---------------------------------------------------------------------------

class TestStripGeminiNotesContent:

    def test_strips_header_block(self):
        content = "## Meeting Title\n\nInvited\n\nAttachments\n\n### Summary\n\nHello"
        result = stv._strip_gemini_notes_content(content)
        assert result.startswith("### Summary")
        assert "Meeting Title" not in result
        assert "Invited" not in result

    def test_strips_review_boilerplate(self):
        content = "### Summary\n\nHello\n\nYou should review Gemini's notes to make sure they're accurate. Get tips."
        result = stv._strip_gemini_notes_content(content)
        assert "You should review" not in result
        assert "Hello" in result

    def test_strips_quality_survey_boilerplate(self):
        content = "### Summary\n\nHello\n\nHow is the quality of these specific notes? Take a short survey."
        result = stv._strip_gemini_notes_content(content)
        assert "quality of these specific notes" not in result

    def test_strips_decisions_feedback_boilerplate(self):
        content = "### Decisions\n\nDecision one.\n\nWe've updated the Decisions section using your feedback.\n\nLet us know what you think: Helpful or Not Helpful"
        result = stv._strip_gemini_notes_content(content)
        assert "updated the Decisions section" not in result
        assert "Let us know" not in result
        assert "Decision one." in result

    def test_strips_bold_decisions_feedback_variant(self):
        content = "### Summary\n\nX\n\nWe've **updated the Decisions section** using your feedback."
        result = stv._strip_gemini_notes_content(content)
        assert "updated the Decisions section" not in result

    def test_preserves_summary_through_details(self):
        content = "## Title\n\n### Summary\n\nSummary text.\n\n### Decisions\n\nDecision.\n\n### Details\n\nDetails text."
        result = stv._strip_gemini_notes_content(content)
        assert "### Summary" in result
        assert "Summary text." in result
        assert "### Decisions" in result
        assert "### Details" in result

    def test_no_h3_returns_content_unchanged(self):
        # No ### heading — header stripping is a no-op; full content returned
        content = "## Title\n\nInvited\n\nAttachments"
        result = stv._strip_gemini_notes_content(content)
        assert "Title" in result
        assert "Invited" in result


# ---------------------------------------------------------------------------
# fetch_gemini_notes_tab
# ---------------------------------------------------------------------------

class TestFetchGeminiNotesTab:

    def _run_result(self, stdout: str, returncode: int = 0):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        return r

    def test_returns_none_on_command_failure(self):
        with patch.object(stv, '_run', return_value=self._run_result('', returncode=1)):
            assert stv.fetch_gemini_notes_tab('docid123') is None

    def test_returns_none_on_empty_stdout(self):
        with patch.object(stv, '_run', return_value=self._run_result('')):
            assert stv.fetch_gemini_notes_tab('docid123') is None

    def test_returns_none_on_invalid_json(self):
        with patch.object(stv, '_run', return_value=self._run_result('not json')):
            assert stv.fetch_gemini_notes_tab('docid123') is None

    def test_returns_none_when_no_notes_tab(self):
        doc = _make_tab_doc("Transcript", [("NORMAL_TEXT", "text", False)])
        with patch.object(stv, '_run', return_value=self._run_result(json.dumps(doc))):
            assert stv.fetch_gemini_notes_tab('docid123') is None

    def test_extracts_notes_tab_content(self):
        with patch.object(stv, '_run', return_value=self._run_result(json.dumps(NOTES_DOC))):
            result = stv.fetch_gemini_notes_tab('docid123')
        assert result is not None
        assert "### Summary" in result
        assert "Strategic alignment" in result

    def test_strips_header_and_boilerplate(self):
        with patch.object(stv, '_run', return_value=self._run_result(json.dumps(NOTES_DOC))):
            result = stv.fetch_gemini_notes_tab('docid123')
        assert "Randy / James" not in result
        assert "Invited" not in result
        assert "You should review" not in result

    def test_uses_correct_gog_flags(self):
        with patch.object(stv, '_run', return_value=None) as mock_run:
            stv.fetch_gemini_notes_tab('docid123')
        cmd = mock_run.call_args[0][0]
        assert '--tab' in cmd
        assert 'Notes' in cmd
        assert '--json' in cmd
        assert '--results-only' in cmd

    def test_returns_none_when_notes_tab_body_empty(self):
        doc = {"tabs": [{"tabProperties": {"title": "Notes"}, "documentTab": {"body": {"content": []}}}]}
        with patch.object(stv, '_run', return_value=self._run_result(json.dumps(doc))):
            assert stv.fetch_gemini_notes_tab('docid123') is None


# ---------------------------------------------------------------------------
# update_meeting_with_gemini_notes
# ---------------------------------------------------------------------------

class TestUpdateMeetingWithGeminiNotes:

    NOTES_CONTENT = "### Summary\n\nStrategic alignment.\n\n### Decisions\n\nDecision one.\n\n### Details\n\nDetails here."

    def _write_meeting(self, tmp_path: Path, body: str) -> Path:
        f = tmp_path / "2026-07-06 - Meeting.md"
        f.write_text(body)
        return f

    def test_returns_false_when_file_missing(self, tmp_path):
        assert stv.update_meeting_with_gemini_notes(tmp_path / "nonexistent.md") is False

    def test_returns_false_when_no_gemini_url(self, tmp_path):
        f = self._write_meeting(tmp_path, "---\nattendees: []\n---\n\n## Actions\n")
        assert stv.update_meeting_with_gemini_notes(f) is False

    def test_returns_false_when_notes_tab_fetch_fails(self, tmp_path):
        f = self._write_meeting(tmp_path, "---\ngemini: https://docs.google.com/document/d/abc123/edit\n---\n\n## Actions\n")
        with patch.object(stv, 'fetch_gemini_notes_tab', return_value=None):
            assert stv.update_meeting_with_gemini_notes(f) is False

    def test_inserts_notes_before_recent_meetings(self, tmp_path):
        body = (
            "---\ngemini: https://docs.google.com/document/d/abc123/edit\n---\n\n"
            "## Actions\n\n## Agenda\n\nAgenda text.\n\n## Recent Meetings\n\nrecent\n"
        )
        f = self._write_meeting(tmp_path, body)
        with patch.object(stv, 'fetch_gemini_notes_tab', return_value=self.NOTES_CONTENT):
            result = stv.update_meeting_with_gemini_notes(f)
        assert result is True
        content = f.read_text()
        gemini_pos = content.find("## Notes by Gemini")
        recent_pos = content.find("## Recent Meetings")
        assert gemini_pos != -1
        assert gemini_pos < recent_pos

    def test_inserts_notes_after_agenda_when_no_recent_meetings(self, tmp_path):
        body = (
            "---\ngemini: https://docs.google.com/document/d/abc123/edit\n---\n\n"
            "## Actions\n\n## Agenda\n\nAgenda text.\n"
        )
        f = self._write_meeting(tmp_path, body)
        with patch.object(stv, 'fetch_gemini_notes_tab', return_value=self.NOTES_CONTENT):
            stv.update_meeting_with_gemini_notes(f)
        content = f.read_text()
        agenda_pos = content.find("## Agenda")
        gemini_pos = content.find("## Notes by Gemini")
        assert gemini_pos > agenda_pos

    def test_inserts_full_notes_content(self, tmp_path):
        body = "---\ngemini: https://docs.google.com/document/d/abc123/edit\n---\n\n## Agenda\n"
        f = self._write_meeting(tmp_path, body)
        with patch.object(stv, 'fetch_gemini_notes_tab', return_value=self.NOTES_CONTENT):
            stv.update_meeting_with_gemini_notes(f)
        content = f.read_text()
        assert "## Notes by Gemini" in content
        assert "### Summary" in content
        assert "### Decisions" in content
        assert "### Details" in content
        assert "Strategic alignment." in content

    def test_replaces_existing_notes_section_idempotent(self, tmp_path):
        body = (
            "---\ngemini: https://docs.google.com/document/d/abc123/edit\n---\n\n"
            "## Agenda\n\nAgenda text.\n\n"
            "## Notes by Gemini\n\n### Summary\n\nOld summary.\n\n"
            "## Recent Meetings\n\nrecent\n"
        )
        f = self._write_meeting(tmp_path, body)
        with patch.object(stv, 'fetch_gemini_notes_tab', return_value=self.NOTES_CONTENT):
            stv.update_meeting_with_gemini_notes(f)
        content = f.read_text()
        assert content.count("## Notes by Gemini") == 1
        assert "Old summary." not in content
        assert "Strategic alignment." in content
        assert "## Recent Meetings" in content

    def test_recent_meetings_section_preserved_after_insert(self, tmp_path):
        body = (
            "---\ngemini: https://docs.google.com/document/d/abc123/edit\n---\n\n"
            "## Agenda\n\n## Recent Meetings\n\nrecent content\n"
        )
        f = self._write_meeting(tmp_path, body)
        with patch.object(stv, 'fetch_gemini_notes_tab', return_value=self.NOTES_CONTENT):
            stv.update_meeting_with_gemini_notes(f)
        content = f.read_text()
        assert "recent content" in content


# ---------------------------------------------------------------------------
# fetch_and_parse_transcript_tab (--json flag regression)
# ---------------------------------------------------------------------------

class TestFetchTranscriptTabFlags:

    def test_uses_json_flag(self):
        with patch.object(stv, '_run', return_value=None) as mock_run:
            stv.fetch_and_parse_transcript_tab('docid123')
        cmd = mock_run.call_args[0][0]
        assert '--json' in cmd
        assert '--tab' in cmd
        assert 'Transcript' in cmd
        assert '--results-only' in cmd
