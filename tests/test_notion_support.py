"""Tests for Notion link detection and heading-level-agnostic section handling."""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import list_notion_meetings
import vault_utils
import write_notion_section
from vault_utils import extract_notion_links, upsert_section

COMMON = "https://app.notion.com/p/supabase/Common-Hours-3f85004b775f801cba43c2d01732ce01"
COMMON_ID = "3f85004b775f801cba43c2d01732ce01"


class TestExtractNotionLinks:
    def test_anchor_with_custom_text(self):
        links = extract_notion_links(f'<a href="{COMMON}">Agenda</a>')
        assert links == [{"url": COMMON, "page_id": COMMON_ID, "is_database": False}]

    def test_bare_url_after_label(self):
        text = "Agenda:\xa0https://www.notion.so/supabase/Compute-weekly-sync-28f5004b775f80dca66ce98241851d26"
        links = extract_notion_links(text)
        assert len(links) == 1
        assert links[0]["page_id"] == "28f5004b775f80dca66ce98241851d26"

    def test_tracking_params_stripped_and_amp_unescaped(self):
        text = (
            '<a href="https://www.notion.so/supabase/Lovable-x-Supabase-Weekly-Technical-Sync-Notes-'
            '2935004b775f804e97a0cb95d9d71b1a?source=copy_link&amp;utm_content=x&amp;pvs=6">Agenda</a>'
        )
        links = extract_notion_links(text)
        assert links[0]["url"] == (
            "https://www.notion.so/supabase/Lovable-x-Supabase-Weekly-Technical-Sync-Notes-"
            "2935004b775f804e97a0cb95d9d71b1a"
        )

    def test_google_redirect_wrapper_unwrapped_and_deduped(self):
        target = "https://app.notion.com/p/supabase/v3-weekly-sync-3b35004b775f8062b7d2f7dc5c43e066"
        wrapped = (
            f"https://www.google.com/url?q={target}?source%3Dcopy_link"
            "&amp;sa=D&amp;source=calendar&amp;ust=1&amp;usg=abc"
        )
        text = f'<a href="{wrapped}">{wrapped}</a>'
        links = extract_notion_links(text)
        assert len(links) == 1
        assert links[0]["url"] == target
        assert links[0]["page_id"] == "3b35004b775f8062b7d2f7dc5c43e066"

    def test_id_only_database_view_flagged(self):
        text = ("https://app.notion.com/p/supabase/3905004b775f809bb964c77e65c22687"
                "?v=3905004b775f80258f50000c8cc1ad2e")
        links = extract_notion_links(text)
        assert links[0]["is_database"] is True
        assert links[0]["url"].endswith("?v=3905004b775f80258f50000c8cc1ad2e")

    def test_pages_sorted_before_databases(self):
        text = (
            "https://app.notion.com/p/supabase/3905004b775f809bb964c77e65c22687?v=3905004b775f80258f50000c8cc1ad2e "
            + COMMON
        )
        links = extract_notion_links(text)
        assert [l["is_database"] for l in links] == [False, True]

    def test_no_notion_links(self):
        assert extract_notion_links("Join https://meet.google.com/abc-defg-hij") == []
        assert extract_notion_links("") == []


H2_TEMPLATE = "## Actions\n\n## Agenda\n\nstuff\n\n## Recent Meetings\n\n```base\n```\n"
H1_TEMPLATE = "# Actions\n\n# Agenda\n\nstuff\n\n# Recent Meetings\n\n```base\n```\n"


class TestUpsertSection:
    def test_inserts_before_recent_meetings_h2(self):
        out = upsert_section(H2_TEMPLATE, "Notes from Notion", "body")
        assert out.index("## Notes from Notion") < out.index("## Recent Meetings")
        assert out.index("## Notes from Notion") > out.index("## Agenda")

    def test_inserts_before_recent_meetings_h1_template(self):
        out = upsert_section(H1_TEMPLATE, "Notes from Notion", "body")
        assert out.index("## Notes from Notion") < out.index("# Recent Meetings")
        assert out.count("Recent Meetings") == 1

    def test_inserts_after_agenda_h1_without_recent_meetings(self):
        out = upsert_section("# Actions\n\n# Agenda\n\ntext\n", "Notes from Notion", "body")
        assert out.index("## Notes from Notion") > out.index("# Agenda")

    def test_replace_in_place_is_idempotent_and_keeps_other_content(self):
        once = upsert_section(H2_TEMPLATE, "Notes from Notion", "old")
        twice = upsert_section(once, "Notes from Notion", "new")
        assert "old" not in twice and "new" in twice
        assert twice.count("## Notes from Notion") == 1
        assert "stuff" in twice and "## Recent Meetings" in twice
        assert upsert_section(twice, "Notes from Notion", "new") == twice

    def test_appends_when_no_anchor(self):
        out = upsert_section("hello\n", "Notes from Notion", "body")
        assert out.endswith("## Notes from Notion\n\nbody\n")


class TestExtractSectionAnyLevel:
    def test_reads_h1_and_h2(self):
        assert vault_utils.extract_section_any_level(H1_TEMPLATE, "Agenda") == "stuff"
        assert vault_utils.extract_section_any_level(H2_TEMPLATE, "Agenda") == "stuff"


class TestScripts:
    def _meeting(self, tmp_path, name, notion):
        folder = tmp_path / "MEETINGS" / "2026" / "11-November"
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / f"2026-11-09 - {name}.md"
        fm = f"---\nstart: 2026-11-09T09:00:00-05:00\n{('notion: ' + notion + chr(10)) if notion else ''}---\n\n{H2_TEMPLATE}"
        f.write_text(fm)
        return f

    def test_list_notion_meetings(self, tmp_path):
        self._meeting(tmp_path, "With Notion", COMMON)
        self._meeting(tmp_path, "Without", "")
        self._meeting(tmp_path, "DB", "https://app.notion.com/p/supabase/3905004b775f809bb964c77e65c22687?v=3905004b775f80258f50000c8cc1ad2e")
        out = list_notion_meetings.list_notion_meetings(tmp_path, datetime(2026, 11, 9))
        assert [m["stem"] for m in out] == ["2026-11-09 - DB", "2026-11-09 - With Notion"]
        by = {m["stem"]: m for m in out}
        assert by["2026-11-09 - DB"]["is_database"] is True
        assert by["2026-11-09 - With Notion"]["page_id"] == COMMON_ID
        assert by["2026-11-09 - With Notion"]["has_section"] is False
        json.dumps(out)

    def test_write_notion_section_cli(self, tmp_path, monkeypatch):
        f = self._meeting(tmp_path, "With Notion", COMMON)
        content = tmp_path / "c.md"
        content.write_text("- topic one\n")
        monkeypatch.setattr(sys, "argv", ["x", "--meeting-file", str(f), "--content-file", str(content)])
        assert write_notion_section.main() == 0
        text = f.read_text()
        assert "## Notes from Notion\n\n- topic one" in text
        content.write_text("- topic two\n")
        assert write_notion_section.main() == 0
        text = f.read_text()
        assert "topic two" in text and "topic one" not in text
        assert text.count("## Notes from Notion") == 1
