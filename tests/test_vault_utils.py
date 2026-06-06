"""Tests for vault_utils.py shared utilities."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import vault_utils


class TestSanitizeTitle:
    def test_replaces_slash(self):
        assert vault_utils.sanitize_title("A/B") == "A - B"

    def test_replaces_colon(self):
        assert vault_utils.sanitize_title("A: B") == "A - B"

    def test_replaces_pipe(self):
        assert vault_utils.sanitize_title("A|B") == "A - B"

    def test_collapses_spaces(self):
        assert vault_utils.sanitize_title("A  B") == "A B"

    def test_strips_whitespace(self):
        assert vault_utils.sanitize_title("  Title  ") == "Title"

    def test_normal_title_unchanged(self):
        assert vault_utils.sanitize_title("Team Standup") == "Team Standup"

    def test_complex_title(self):
        result = vault_utils.sanitize_title("Test User & Alice: 1:1")
        assert "/" not in result
        assert ":" not in result


class TestHtmlToMarkdown:
    def test_converts_link(self):
        html = '<a href="https://example.com">Click here</a>'
        assert "[Click here](https://example.com)" in vault_utils.html_to_markdown(html)

    def test_converts_br(self):
        result = vault_utils.html_to_markdown("Line 1<br>Line 2")
        assert "\n" in result

    def test_strips_tags(self):
        result = vault_utils.html_to_markdown("<p>Hello <b>world</b></p>")
        assert "<" not in result
        assert "Hello" in result
        assert "world" in result

    def test_decodes_entities(self):
        result = vault_utils.html_to_markdown("AT&amp;T &lt;test&gt;")
        assert "AT&T" in result
        assert "<test>" in result

    def test_empty_string(self):
        assert vault_utils.html_to_markdown("") == ""


class TestExtractDocIdFromUrl:
    def test_standard_url(self):
        url = "https://docs.google.com/document/d/ABC123xyz/edit"
        assert vault_utils.extract_doc_id_from_url(url) == "ABC123xyz"

    def test_url_without_edit(self):
        url = "https://docs.google.com/document/d/ABC123xyz"
        assert vault_utils.extract_doc_id_from_url(url) == "ABC123xyz"

    def test_non_gdoc_url(self):
        assert vault_utils.extract_doc_id_from_url("https://example.com") is None

    def test_empty_string(self):
        assert vault_utils.extract_doc_id_from_url("") is None


class TestParseFrontmatter:
    def test_basic_frontmatter(self):
        content = "---\ntitle: My Note\ntags:\n  - Test\n---\nBody"
        fm = vault_utils.parse_frontmatter(content)
        assert fm["title"] == "My Note"

    def test_no_frontmatter(self):
        assert vault_utils.parse_frontmatter("No frontmatter here") == {}

    def test_unclosed_frontmatter(self):
        assert vault_utils.parse_frontmatter("---\ntitle: Oops") == {}

    def test_iso_datetime(self):
        content = "---\nstart: 2026-06-05T09:00:00-04:00\n---"
        fm = vault_utils.parse_frontmatter(content)
        assert "2026-06-05" in fm["start"]


class TestUpdateFrontmatterWithMissingProperties:
    def test_adds_missing_key(self):
        content = "---\ntitle: Test\n---\nBody"
        updated = vault_utils.update_frontmatter_with_missing_properties(content, {"gmeet": "https://meet.google.com/abc"})
        assert "gmeet: https://meet.google.com/abc" in updated

    def test_does_not_overwrite_existing(self):
        content = "---\ngmeet: https://meet.google.com/original\n---\nBody"
        updated = vault_utils.update_frontmatter_with_missing_properties(content, {"gmeet": "https://meet.google.com/new"})
        assert "original" in updated
        assert "new" not in updated

    def test_no_frontmatter_returns_unchanged(self):
        content = "No frontmatter"
        assert vault_utils.update_frontmatter_with_missing_properties(content, {"key": "val"}) == content


class TestUpdateFrontmatterValues:
    def test_updates_existing_key(self):
        content = "---\nstart: 2026-06-05T08:00:00-04:00\n---\nBody"
        updated = vault_utils.update_frontmatter_values(content, {"start": "2026-06-05T09:00:00-04:00"})
        assert "09:00:00" in updated
        assert "08:00:00" not in updated

    def test_does_not_add_new_key(self):
        content = "---\ntitle: Test\n---\nBody"
        updated = vault_utils.update_frontmatter_values(content, {"newkey": "val"})
        assert "newkey" not in updated

    def test_unchanged_when_value_same(self):
        content = "---\nstart: 2026-06-05T09:00:00-04:00\n---"
        updated = vault_utils.update_frontmatter_values(content, {"start": "2026-06-05T09:00:00-04:00"})
        assert updated == content


class TestExtractSection:
    def test_extracts_h2_section(self):
        content = "## Actions\n\n- [ ] Do something\n\n## Agenda\n\n1. Topic"
        result = vault_utils.extract_section(content, "Actions", level=2)
        assert "Do something" in result
        assert "Topic" not in result

    def test_returns_empty_when_not_found(self):
        content = "## Other Section\n\nSome content"
        assert vault_utils.extract_section(content, "Missing", level=2) == ""

    def test_extracts_h3_section(self):
        content = "## Notes by Gemini\n\n### Summary\n\nMeeting went well.\n\n### Details\n\nMore info"
        result = vault_utils.extract_section(content, "Summary", level=3)
        assert "Meeting went well" in result
        assert "More info" not in result


class TestExtractParkingLot:
    def test_extracts_bullets(self, mock_vault):
        content = (mock_vault / "PEOPLE" / "Alice Tester.md").read_text()
        items = vault_utils.extract_parking_lot(content)
        assert len(items) == 2
        assert "Discuss Q3 project priorities" in items

    def test_empty_when_no_section(self):
        content = "---\ntags:\n  - People\n---\n# Bio\n\nSome bio text"
        assert vault_utils.extract_parking_lot(content) == []

    def test_handles_emoji_variant(self):
        content = "# Parking Lot  🚗\n\n- Item one\n- Item two"
        items = vault_utils.extract_parking_lot(content)
        assert "Item one" in items
        assert "Item two" in items

    def test_ignores_nested_bullets(self):
        content = "# Parking Lot\n\n- Top level\n  - Nested (should be ignored)\n- Also top"
        items = vault_utils.extract_parking_lot(content)
        assert "Top level" in items
        assert "Also top" in items
        # Nested bullets start with spaces — re.match on '^[-*]\s+' won't match '  - Nested'
        assert not any("Nested" in i for i in items)


class TestExtractGeminiSummary:
    def test_extracts_summary(self, mock_vault):
        content = (mock_vault / "MEETINGS" / "2026" / "05-May" / "2026-05-20 - Test User & Alice Tester.md").read_text()
        summary = vault_utils.extract_gemini_summary(content)
        assert "Q2 priorities" in summary

    def test_returns_empty_without_gemini_section(self):
        content = "## Actions\n\n- [ ] Do stuff\n\n## Agenda\n\n1. Topic"
        assert vault_utils.extract_gemini_summary(content) == ""
