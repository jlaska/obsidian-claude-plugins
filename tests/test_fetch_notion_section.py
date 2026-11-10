"""Tests for fetch_notion_section.py (CLI + run()), using synthetic fixtures."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / "skills" / "daily-planner"
sys.path.insert(0, str(SKILL))
import fetch_notion_section
import vault_utils

FIX = Path(__file__).parent / "fixtures" / "notion"
SCRIPT = SKILL / "fetch_notion_section.py"
PAGE_URL = "https://app.notion.com/p/aaaa0000bbbb1111cccc2222dddd3333"
INDEX_URL = "https://app.notion.com/p/bbbb0000cccc1111dddd2222eeee3333"
CHILD_URL = "https://app.notion.com/p/cccc0000dddd1111eeee2222ffff3333"

MEETING = f"""---
type: meeting
notion: {PAGE_URL}
---

# Example Weekly Sync

## Agenda

- item

## My notes

keep me

---

# Recent Meetings

- earlier
"""


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "PEOPLE").mkdir(parents=True)
    (v / "PEOPLE" / "Alex Example.md").write_text("---\nmail: alex.example@example.com\n---\n# Alex Example\n")
    (v / "PEOPLE" / "Sam Sample.md").write_text("---\ntype: person\n---\n# Sam Sample\n")
    folder = v / "MEETINGS" / "2026" / "11-November"
    folder.mkdir(parents=True)
    (folder / "2026-11-09 - Example Weekly Sync.md").write_text(MEETING)
    return v


@pytest.fixture
def meeting(vault):
    return vault / "MEETINGS" / "2026" / "11-November" / "2026-11-09 - Example Weekly Sync.md"


def cli(meeting, raw_file, date="2026-11-09", *extra):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--meeting-file", str(meeting), "--date", date,
         "--raw-file", str(raw_file), *extra],
        capture_output=True, text=True)
    lines = [l for l in proc.stdout.split("\n") if l]
    assert len(lines) == 1, f"stdout must be exactly one line: {proc.stdout!r}"
    return proc.returncode, json.loads(lines[0]), proc.stderr


def raw(name):
    return FIX / name


class TestWritten:
    def test_writes_section_with_source_line(self, meeting):
        code, out, _ = cli(meeting, raw("running_doc.json"))
        assert code == 0
        assert out["status"] == "written"
        assert out["matched_dates"] == ["2026-11-09"]
        assert out["child_candidate"] is None
        assert set(out) == {"status", "reason", "matched_dates", "child_candidate", "warnings"}
        text = meeting.read_text()
        assert f"## Notes from Notion\n\nSource: [Notion]({PAGE_URL})\n\n### 2026-11-09 EMEA/AMER" in text
        assert text.index("## Notes from Notion") < text.index("# Recent Meetings")
        assert "keep me" in text and "- earlier" in text

    def test_idempotent_rewrite(self, meeting):
        cli(meeting, raw("running_doc.json"))
        first = meeting.read_text()
        code, out, _ = cli(meeting, raw("running_doc.json"))
        assert code == 0 and out["status"] == "written"
        assert meeting.read_text() == first
        assert first.count("## Notes from Notion") == 1

    def test_replace_in_place_when_content_changes(self, meeting):
        cli(meeting, raw("running_doc.json"), "2026-11-09")
        cli(meeting, raw("running_doc.json"), "2026-11-02")
        text = meeting.read_text()
        assert text.count("## Notes from Notion") == 1
        assert "Monday notes" in text and "Topics" not in text
        assert "keep me" in text and "- earlier" in text

    def test_text_only_raw_file(self, meeting):
        code, out, _ = cli(meeting, raw("running_doc.txt"))
        assert code == 0 and out["status"] == "written"

    def test_notion_url_override(self, meeting):
        cli(meeting, raw("running_doc.json"), "2026-11-09", "--notion-url", "https://example.com/override")
        assert "Source: [Notion](https://example.com/override)" in meeting.read_text()

    def test_child_page_uses_child_url_for_source(self, meeting):
        code, out, _ = cli(meeting, raw("child_table.json"))
        assert out["status"] == "written"
        assert f"Source: [Notion]({CHILD_URL})" in meeting.read_text()
        assert PAGE_URL not in meeting.read_text().split("## Notes from Notion")[1]

    def test_person_mentions_become_wikilinks_only_with_people_note(self, meeting, vault):
        code, out, _ = cli(meeting, raw("child_table.json"), "2026-11-09", "--vault-root", str(vault))
        assert out["status"] == "written"
        text = meeting.read_text()
        assert "[[Alex Example]]" in text      # matched by mail: frontmatter
        assert "[[Sam Sample]]" in text        # matched by email-derived name
        assert "sam.sample@example.com" not in text

    def test_unmatched_person_stays_plain_and_gog_never_called(self, meeting, vault, monkeypatch):
        import sync_to_vault
        calls = []
        real = sync_to_vault._run

        def spy(cmd, timeout=30):
            calls.append(cmd[0])
            return real(cmd, timeout)

        monkeypatch.setattr(sync_to_vault, "_run", spy)
        (vault / "PEOPLE" / "Alex Example.md").unlink()
        (vault / "PEOPLE" / "Sam Sample.md").unlink()
        out = fetch_notion_section.run(meeting, "2026-11-09", raw("child_table.json").read_text(),
                                       vault_root=vault)
        assert out["status"] == "written"
        text = meeting.read_text()
        assert "Alex Example & Sam Sample" in text and "[[" not in text
        assert "gog" not in calls
        assert sync_to_vault._run is spy  # patch restored

    def test_vault_root_inferred_from_meeting_path(self, meeting):
        code, out, _ = cli(meeting, raw("child_table.json"))
        assert "[[Alex Example]]" in meeting.read_text()


class TestNoWrite:
    def _unchanged(self, meeting, raw_file, date="2026-11-09"):
        before = meeting.read_text()
        code, out, err = cli(meeting, raw_file, date)
        assert meeting.read_text() == before
        return code, out, err

    def test_no_section_future_only(self, meeting, tmp_path):
        f = tmp_path / "r.json"
        f.write_text(json.dumps({"text": '<content>\n# <date start="2026-12-25" /> x\n\ntext\n</content>'}))
        code, out, _ = self._unchanged(meeting, f)
        assert code == 0 and out["status"] == "no_section" and out["reason"]

    def test_no_section_too_old(self, meeting):
        code, out, _ = self._unchanged(meeting, raw("running_doc.json"), "2026-12-31")
        assert code == 0 and out["status"] == "no_section"

    def test_no_section_does_not_remove_existing_section(self, meeting):
        cli(meeting, raw("running_doc.json"))
        written = meeting.read_text()
        code, out, _ = cli(meeting, raw("running_doc.json"), "2026-12-31")
        assert out["status"] == "no_section"
        assert meeting.read_text() == written

    def test_needs_child(self, meeting):
        code, out, _ = self._unchanged(meeting, raw("index_page.json"))
        assert code == 0 and out["status"] == "needs_child"
        assert out["child_candidate"] == {"title": "11/09 | Example Stand-Up", "url": CHILD_URL}
        assert out["matched_dates"] == ["2026-11-09"]

    def test_needs_child_then_child_rerun_writes(self, meeting):
        cli(meeting, raw("index_page.json"))
        code, out, _ = cli(meeting, raw("child_table.json"))
        assert out["status"] == "written"
        assert "| Workstream |" in meeting.read_text()

    def test_error_json_nonzero_exit(self, meeting):
        code, out, err = self._unchanged(meeting, raw("error_not_found.json"))
        assert code != 0
        assert out["status"] == "error" and "object_not_found" in out["reason"]
        assert "object_not_found" in err

    def test_missing_raw_file(self, meeting, tmp_path):
        code, out, _ = self._unchanged(meeting, tmp_path / "nope.txt")
        assert code != 0 and out["status"] == "error"

    def test_missing_meeting_file(self, tmp_path):
        code, out, _ = cli(tmp_path / "nope.md", raw("running_doc.json"))
        assert code != 0 and out["status"] == "error"

    def test_bad_date(self, meeting):
        code, out, _ = self._unchanged(meeting, raw("running_doc.json"), "yesterday")
        assert code != 0 and out["status"] == "error"

    def test_garbage_raw_file(self, meeting, tmp_path):
        f = tmp_path / "junk.txt"
        f.write_text("<<< not notion at all")
        code, out, _ = self._unchanged(meeting, f)
        assert code != 0 and out["status"] == "error"

    def test_raw_file_is_required(self, meeting):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--meeting-file", str(meeting),
                               "--date", "2026-11-09"], capture_output=True, text=True)
        assert proc.returncode != 0


class TestSectionCompat:
    def test_written_section_matches_write_notion_section_semantics(self, meeting):
        """Replace-in-place must agree with vault_utils.upsert_section directly."""
        cli(meeting, raw("running_doc.json"))
        text = meeting.read_text()
        body = text.split("## Notes from Notion\n\n", 1)[1].split("\n\n# Recent Meetings", 1)[0]
        assert vault_utils.upsert_section(text, "Notes from Notion", body) == text


class TestNtnMode:
    NTN_PAGE = (
        '---\ntitle: Syncs\n---\n\n'
        '## <mention-date start="2026-11-09"/> \n1. Alex Example to follow up\n\n'
        '## <mention-date start="2026-11-02"/> \n1. Older\n'
    )

    def _meeting(self, tmp_path, url='https://www.notion.so/ws/Syncs-0123456789abcdef0123456789abcdef'):
        f = tmp_path / '2026-11-09 - Sync.md'
        f.write_text(f'---\nnotion: {url}\n---\n\n## Actions\n\n## Agenda\n')
        return f

    def test_ntn_fetch_writes_section(self, tmp_path):
        from fetch_notion_section import run_ntn
        f = self._meeting(tmp_path)
        seen = []
        out = run_ntn(f, '2026-11-09', getter=lambda pid: seen.append(pid) or self.NTN_PAGE)
        assert out['status'] == 'written'
        assert seen == ['0123456789abcdef0123456789abcdef']
        text = f.read_text()
        assert '## Notes from Notion' in text and 'Alex Example to follow up' in text
        assert 'Older' not in text

    def test_ntn_failure_is_error_and_does_not_write(self, tmp_path):
        from fetch_notion_section import run_ntn
        f = self._meeting(tmp_path)
        before = f.read_text()

        def boom(pid):
            raise RuntimeError('ntn failed: 404')
        out = run_ntn(f, '2026-11-09', getter=boom)
        assert out['status'] == 'error' and '404' in out['reason']
        assert f.read_text() == before

    def test_ntn_database_link_rejected(self, tmp_path):
        from fetch_notion_section import run_ntn
        f = self._meeting(tmp_path, 'https://www.notion.so/ws/0123456789abcdef0123456789abcdef?v=fedcba9876543210fedcba9876543210')
        out = run_ntn(f, '2026-11-09', getter=lambda pid: 'x')
        assert out['status'] == 'error'

    def test_ntn_index_page_follows_child(self, tmp_path):
        from fetch_notion_section import run_ntn
        f = self._meeting(tmp_path)
        child_url = 'https://app.notion.com/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        index = ('---\ntitle: Index\n---\n\n'
                 f'<page url="{child_url}">11/09 \\| Stand-Up</page>\n')
        pages = {'0123456789abcdef0123456789abcdef': index,
                 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa': '---\ntitle: 11/09 | Stand-Up\n---\n\nChild body line\n'}
        out = run_ntn(f, '2026-11-09', getter=lambda pid: pages[pid])
        assert out['status'] == 'written'
        assert 'Child body line' in f.read_text()
