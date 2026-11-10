"""Tests for the deterministic Notion -> Obsidian converter (synthetic fixtures only)."""

import json
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "daily-planner"))
import notion_convert
from notion_convert import convert

FIX = Path(__file__).parent / "fixtures" / "notion"
PID = "aaaa0000bbbb1111cccc2222dddd3333"
UU = "11111111-2222-3333-4444-555555555555"


def load(name):
    return (FIX / name).read_text()


def mk(body, title="Example Page", props=None):
    """Build a raw notion-fetch JSON string around a body."""
    url = f"https://app.notion.com/p/{PID}"
    props = props or json.dumps({"title": title})
    text = (f'Here is the result of "fetch" for the Page with URL {url} as of 2026-11-09:\n'
            f'<page url="{url}">\n<ancestor-path></ancestor-path>\n<properties>\n{props}\n'
            f'</properties>\n<iconMetadata>null</iconMetadata>\n<content>\n{body}\n</content>\n</page>')
    return json.dumps({"metadata": {"type": "page"}, "title": title, "url": url, "text": text})


def md(body, date=None, **kw):
    """Convert a body with no dated headings and return the markdown minus the title line."""
    res = convert(mk(body), date, **kw)
    assert res["status"] == "ok", res
    out = res["markdown"]
    return out.split("\n\n", 1)[1] if out.startswith("### Example Page") else out


def dated(sections):
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

class TestEnvelope:
    def test_whole_json_and_text_only_give_same_markdown(self):
        a = convert(load("running_doc.json"), "2026-11-09")
        b = convert(load("running_doc.txt"), "2026-11-09")
        assert a["status"] == b["status"] == "ok"
        assert a["markdown"] == b["markdown"]

    def test_accepts_dict_input(self):
        res = convert(json.loads(load("running_doc.json")), "2026-11-09")
        assert res["status"] == "ok"

    def test_toplevel_title_preferred(self):
        raw = json.loads(mk("hello", title="Top Title"))
        raw["text"] = raw["text"].replace('"title": "Top Title"', '"title": "Props Title"')
        assert convert(raw)["title"] == "Top Title"

    def test_title_from_properties_html_text_only(self):
        res = convert(json.loads(load("child_table.json"))["text"], "2026-11-09")
        assert res["title"] == "11/09 | Example Stand-Up"

    def test_title_html_stripped_and_pipe_unescaped(self):
        raw = json.loads(mk("x"))
        raw["title"] = "<p>\n11/09 \\| A \\[b\\]\n</p>"
        assert convert(raw)["title"] == "11/09 | A [b]"

    def test_error_json(self):
        res = convert(load("error_not_found.json"), "2026-11-09")
        assert res["status"] == "error"
        assert "object_not_found" in res["reason"]
        assert "Could not find page" in res["reason"]
        assert res["markdown"] == ""

    @pytest.mark.parametrize("raw", ["", "   ", "not a notion page", "{}", "[]", "[1,2]", None, 5,
                                     '{"text": "no content tag here"}'])
    def test_unrecognised_input_is_error_not_exception(self, raw):
        assert convert(raw, "2026-11-09")["status"] == "error"

    def test_content_between_first_open_and_last_close(self):
        body = "inner <content>literal</content> text"
        assert "literal" in md(body)

    def test_missing_close_content_warns(self):
        text = "<page><content>\nhello there\n"
        res = convert(text)
        assert res["status"] == "ok" and "hello there" in res["markdown"]
        assert any("</content>" in w for w in res["warnings"])

    def test_invalid_date_is_error(self):
        assert convert(mk("x"), "11/09/2026")["status"] == "error"


# ---------------------------------------------------------------------------
# Constructs
# ---------------------------------------------------------------------------

class TestDroppedAndWrapperTags:
    def test_dropped_constructs(self):
        out = md('<unknown url="u" alt="button" />\n<unknown url="u" alt="alias"/>\n<p />\n'
                 '<table-of-contents color="gray" />\n<embed source="x" />\nkept')
        assert out == "kept"

    def test_synced_block_children_kept(self):
        assert md("<synced-block>\nchild text\n</synced-block>") == "child text"

    def test_span_inserts_no_whitespace(self):
        out = md('<span discussion-urls="[]">wor</span><span discussion-urls="[]">d</span>s')
        assert out == "words"

    def test_unknown_tags_keep_inner_text(self):
        assert md("<weird attr='x'>inner</weird> and <foo>more") == "inner and more"


class TestMentionsAndLinks:
    def test_user_mention_plain_name_without_resolver(self):
        out = md(f'<mention url="user://{UU}/alex.example@example.com">Alex Example</mention> hi')
        assert out == "Alex Example hi"

    def test_user_mention_resolved_to_wikilink(self):
        seen = []

        def resolver(email, name):
            seen.append((email, name))
            return "[[Alex Example]]" if email == "alex.example@example.com" else None

        out = md(f'<mention url="user://{UU}/alex.example@example.com">Alex</mention> and '
                 f'<mention url="user://{UU}/other@example.com">Pat Other</mention>',
                 resolve_person=resolver)
        assert out == "[[Alex Example]] and Pat Other"
        assert ("alex.example@example.com", "Alex") in seen

    def test_resolver_exception_falls_back_to_name(self):
        def boom(email, name):
            raise RuntimeError("nope")
        res = convert(mk(f'<mention url="user://{UU}/a@example.com">Alex</mention>'),
                      resolve_person=boom)
        assert "Alex" in res["markdown"]
        assert any("resolve_person failed" in w for w in res["warnings"])

    def test_resolver_called_once_per_person(self):
        calls = []
        body = (f'<mention url="user://{UU}/a@example.com">A</mention> '
                f'<mention url="user://{UU}/a@example.com">A</mention>')
        md(body, resolve_person=lambda e, n: calls.append(e) or None)
        assert calls == ["a@example.com"]

    def test_page_mention_is_link(self):
        out = md('<mention url="https://app.notion.com/p/abc">Planning Doc</mention>')
        assert out == "[Planning Doc](https://app.notion.com/p/abc)"

    def test_self_closing_mention_keeps_url(self):
        out = md('see <mention url="https://app.notion.com/p/abc"/>.')
        assert out == "see [Notion page](https://app.notion.com/p/abc)."

    def test_link_preview_uses_source_not_url(self):
        out = md('<link-preview url="https://app.notion.com/p/x#blk" source="https://docs.example.com/a?x=1&amp;y=2" />')
        assert out == "[docs.example.com](https://docs.example.com/a?x=1&y=2)"
        assert "#blk" not in out

    def test_link_preview_linear_uses_issue_key(self):
        out = md('<link-preview url="u" source="https://linear.app/ws/issue/ABC-123/some-slug" />')
        assert out == "[ABC-123](https://linear.app/ws/issue/ABC-123/some-slug)"

    def test_link_preview_without_source_dropped(self):
        assert md('before <link-preview url="https://app.notion.com/p/x#b" /> after') == "before  after"

    def test_inline_date(self):
        assert md('due <date start="2026-12-01" /> ok') == "due 2026-12-01 ok"

    def test_link_with_code_label(self):
        out = md("[<code>main</code>](https://example.com/x)")
        assert out == "[`main`](https://example.com/x)"

    def test_link_text_equal_url_collapses(self):
        out = md("[https://example.com/a_b](https://example.com/a_b) and [x](https://example.com/y)")
        assert out == "<https://example.com/a_b> and [x](https://example.com/y)"

    def test_slack_message_converted(self):
        raw = "slackMessage://example.slack.com/C0123ABC/1790000000.123456/1790000000.123456"
        out = md(f"[{raw}]({raw})")
        assert out == "[Slack thread](https://example.slack.com/archives/C0123ABC/p1790000000123456)"

    def test_slack_thread_reply_gets_thread_ts(self):
        raw = "slackMessage://example.slack.com/C0123ABC/1790000200.000100/1790000000.123456"
        out = md(f"[{raw}]({raw})")
        assert out == ("[Slack thread](https://example.slack.com/archives/C0123ABC/"
                       "p1790000200000100?thread_ts=1790000000.123456)")

    def test_slack_custom_label_kept_and_bare_url(self):
        raw = "slackMessage://example.slack.com/C0123ABC/1790000000.123456/1790000000.123456"
        out = md(f"([my label]({raw})) and {raw}")
        assert "[my label](https://example.slack.com/archives/C0123ABC/p1790000000123456)" in out
        assert out.endswith("and https://example.slack.com/archives/C0123ABC/p1790000000123456")

    def test_image_and_file(self):
        out = md('<image source="notion-file-block://a-b/c?space_id=s&name=Shot 1.png" />\n'
                 '<file source="notion-file-block://a-b/c?space_id=s&amp;name=r.pdf"></file>\n'
                 '<image source="notion-file-block://a-b/c" />')
        assert out.split("\n") == ["[image: Shot 1.png]", "[file: r.pdf]", "[image]"]

    def test_child_page_entries_become_links(self):
        out = md('<page url="https://app.notion.com/p/c1">\nAlpha \\| Beta \n</page>\n'
                 '<page url="https://app.notion.com/p/c2">\nGamma\n</page>')
        assert out == "- [Alpha | Beta](https://app.notion.com/p/c1)\n- [Gamma](https://app.notion.com/p/c2)"


class TestInlineFormatting:
    def test_bold_italic_strike(self):
        assert md("<b>a</b> <i>b</i> <s>c</s>") == "**a** *b* ~~c~~"

    def test_partial_word_bold_gains_no_spaces(self):
        assert md("un<b>bold</b>ed") == "un**bold**ed"

    def test_bold_whitespace_moved_outside(self):
        assert md("<b>Name </b>next") == "**Name** next"

    def test_empty_bold_dropped(self):
        assert md("a<b></b>b") == "ab"

    def test_code_plain_and_with_backtick(self):
        assert md("<code>x</code> <code>a`b</code> <code>`z</code>") == "`x` ``a`b`` `` `z ``"

    def test_escapes_unescaped_outside_code_and_urls(self):
        out = md(r"\[a\] \_b\_ \~c\~ \<d\> \| \{e\} \$5 \*f\* `\_keep\_` [l\_x](https://example.com/p\_q)")
        assert out == "[a] _b_ ~c~ <d> | {e} $5 *f* `\\_keep\\_` [l_x](https://example.com/p\\_q)"

    def test_emoji_shortcodes_stripped_but_times_kept(self):
        out = md(":example-icon: Hello at 10:30:45 and a:b:c")
        assert out == "Hello at 10:30:45 and a:b:c"


class TestLists:
    def test_html_ordered_list_with_empty_item(self):
        out = md("<ol>\n<li>\none\n</li>\n<li></li>\n<li>\ntwo\n</li>\n</ol>")
        assert out == "1. one\n2. two"

    def test_html_unordered(self):
        assert md("<ul>\n<li>\na\n</li>\n<li>\nb\n</li>\n</ul>") == "- a\n- b"

    def test_markdown_ordered_list_renumbered_from_one(self):
        assert md("5. five\n6. six\n10. ten") == "1. five\n2. six\n3. ten"

    def test_checkboxes_preserved_and_empty_items_dropped(self):
        out = md("- [ ] todo\n- [x] done\n- \n- [ ]\n-")
        assert out == "- [ ] todo\n- [x] done"

    def test_nested_markdown_and_html_mix(self):
        body = ("<ol>\n<li>\nouter\n\n1. md child\n   <ol>\n   <li>\n   html grandchild\n   </li>\n   </ol>\n"
                "5. second md child\n</li>\n<li>\nouter two\n</li>\n</ol>")
        assert md(body) == ("1. outer\n"
                            "    1. md child\n"
                            "        1. html grandchild\n"
                            "    2. second md child\n"
                            "2. outer two")

    def test_nested_html_lists_indent(self):
        body = "<ol>\n<li>\na\n<ol>\n<li>\nb\n<ol>\n<li>\nc\n</li>\n</ol>\n</li>\n</ol>\n</li>\n</ol>"
        assert md(body) == "1. a\n    1. b\n        1. c"

    def test_list_item_whose_only_content_is_nested_list(self):
        body = "<ol>\n<li>\n<ol>\n<li>\nonly\n</li>\n</ol>\n</li>\n</ol>"
        assert md(body) == "1. only"

    def test_list_separated_from_paragraph(self):
        assert md("intro\n- a\n- b\noutro") == "intro\n\n- a\n- b\n\noutro"

    def test_horizontal_rule_dropped(self):
        assert "---" not in md("a\n\n---\n\nb")


class TestTablesCalloutsToggles:
    def test_table_header_escape_br_and_empty_cells(self):
        body = ('<table header-row="true">\n<tr>\n<td>\nA\n</td>\n<td>\nB | C\n</td>\n</tr>\n'
                '<tr>\n<td>\nx<br />y\n</td>\n<td></td>\n</tr>\n</table>')
        assert md(body) == "| A | B \\| C |\n| --- | --- |\n| x<br>y |  |"

    def test_table_cells_with_mentions_and_inline(self):
        body = (f'<table>\n<tr>\n<td>\nHead\n</td>\n</tr>\n<tr>\n<td>\n'
                f'<mention url="user://{UU}/a@example.com">Alex</mention> <b>x</b> `c`\n</td>\n</tr>\n</table>')
        assert md(body) == "| Head |\n| --- |\n| Alex **x** `c` |"

    def test_ragged_rows_padded(self):
        body = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>"
        assert md(body) == "| a | b |\n| --- | --- |\n| c |  |"

    def test_callout(self):
        assert md('<callout icon="x">\n<p>\nHeads up\n</p>\n</callout>') == "> [!note] Heads up"

    def test_callout_multiline_and_list(self):
        out = md('<callout>\nline one\nline two\n</callout>\n\n<callout>\n- a\n- b\n</callout>')
        assert out == "> [!note] line one\n> line two\n\n> [!note]\n> - a\n> - b"

    def test_plain_toggle_is_bold_line_plus_content(self):
        assert md("<toggle>\nTitle\n\nbody text\n</toggle>") == "**Title**\n\nbody text"

    def test_heading_toggle_becomes_demoted_heading(self):
        assert md('<toggle heading="h2">\nNEXT\n\n1. a\n</toggle>') == "#### NEXT\n\n1. a"

    def test_table_of_contents_toggle_dropped(self):
        out = md('<toggle>\nTable of contents\n\n<table-of-contents color="gray" />\n</toggle>\nreal')
        assert out == "real"

    def test_code_fence_passthrough(self):
        out = md("before\n\n```python\nx = '<b>' # not a tag\n- not a list\n```\n\nafter")
        assert "```python\nx = '<b>' # not a tag\n- not a list\n```" in out


class TestHeadings:
    def test_headings_demoted_below_section_level(self):
        out = md("<h1>One</h1>\n<h2>Two</h2>\n<h3>Three</h3>\n<h4>Four</h4>\n# H1\n## H2\n### H3\n#### H4")
        assert out.split("\n\n") == ["#### One", "#### Two", "##### Three", "###### Four",
                                     "#### H1", "#### H2", "##### H3", "###### H4"]

    def test_heading_emoji_shortcode_stripped(self):
        assert md("## :example-icon: Decisions") == "#### Decisions"

    def test_output_never_contains_h1_h2_or_rule(self):
        res = convert(load("running_doc.json"), "2026-11-09")
        for line in res["markdown"].split("\n"):
            assert not re.match(r"#{1,2}( |$)", line), line
            assert not line.startswith("---")


# ---------------------------------------------------------------------------
# Section selection
# ---------------------------------------------------------------------------

def sec_h1(d, text, suffix=""):
    return f'# <date start="{d}" />{" " + suffix if suffix else ""}\n\n{text}'


def sec_h2(d, text):
    return f'<h2>\n<date start="{d}" />\n</h2>\n\n{text}'


def sec_toggle(d, text):
    return f'<toggle heading="h2">\n<date start="{d}" /> \n\n{text}\n</toggle>'


class TestSelection:
    def test_exact_match_all_heading_forms(self):
        body = dated([sec_h1("2026-11-09", "from h1"), sec_h2("2026-11-06", "from h2"),
                      sec_toggle("2026-11-02", "from toggle")])
        for d, expect in (("2026-11-09", "from h1"), ("2026-11-06", "from h2"),
                          ("2026-11-02", "from toggle")):
            res = convert(mk(body), d)
            assert res["status"] == "ok"
            assert res["matched_dates"] == [d]
            assert expect in res["markdown"]
            assert res["markdown"].startswith(f"### {d}")
            assert "Notes from" not in res["markdown"]
            assert len(re.findall("from ", res["markdown"])) == 1

    def test_suffix_in_section_title(self):
        res = convert(mk(sec_h1("2026-11-09", "body", "APAC/EMEA")), "2026-11-09")
        assert res["markdown"].startswith("### 2026-11-09 APAC/EMEA\n\nbody")

    def test_same_date_double_sections_all_returned_in_order(self):
        body = dated([sec_h1("2026-10-27", "first body", "EMEA/AMER"),
                      sec_h1("2026-10-27", "second body", "APAC")])
        res = convert(mk(body), "2026-10-27")
        out = res["markdown"]
        assert res["matched_dates"] == ["2026-10-27"]
        assert out.index("### 2026-10-27 EMEA/AMER") < out.index("first body") < \
            out.index("### 2026-10-27 APAC") < out.index("second body")

    def test_mixed_forms_same_page_section_ends_at_next_date_of_any_form(self):
        body = dated([sec_h1("2026-11-09", "nine"), sec_h2("2026-11-06", "six"),
                      sec_h1("2026-11-02", "two")])
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert "nine" in out and "six" not in out
        out = convert(mk(body), "2026-11-06")["markdown"]
        assert "six" in out and "two" not in out and "nine" not in out

    def test_equal_level_subheadings_stay_inside_section(self):
        body = dated([sec_h1("2026-11-09", "## sub heading\n\n### deeper\n\ntext"),
                      sec_h1("2026-11-02", "old")])
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert "#### sub heading" in out and "##### deeper" in out and "text" in out
        assert "old" not in out

    def test_section_ends_at_non_date_h1(self):
        body = dated([sec_h1("2026-11-09", "keep me"), "# Archive\n\nnot mine"])
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert "keep me" in out and "not mine" not in out
        body = dated([sec_h1("2026-11-09", "keep me"), "<h1>Archive</h1>\n\nnot mine"])
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert "keep me" in out and "not mine" not in out

    def test_empty_h1_lines_ignored(self):
        body = dated([sec_h1("2026-11-09", "part one\n\n# \n\npart two")])
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert "part one" in out and "part two" in out

    def test_date_mid_line_is_not_a_heading(self):
        body = dated([sec_h1("2026-11-09", "early\n\n# Title <date start=\"2026-11-10\" /> trailing\n\nlate")])
        # The only "heading" candidate with a mid-line date is a non-date H1 (ends section).
        res = convert(mk(body), "2026-11-09")
        assert res["matched_dates"] == ["2026-11-09"]
        assert "early" in res["markdown"] and "late" not in res["markdown"]
        body2 = 'Meeting on <date start="2026-11-09" /> was moved\n\nmore'
        res2 = convert(mk(body2), "2026-11-09")
        assert res2["status"] == "ok" and res2["matched_dates"] == []  # whole page, no sections

    def test_toggle_section_ends_at_matching_close_and_ignores_undated_toggle(self):
        body = ('<toggle heading="h2">\nNEXT\n\nundated stuff\n</toggle>\n\n' +
                sec_toggle("2026-11-03", "<toggle>\nInner\n\ninner body\n</toggle>\n\nafter inner") +
                "\n\ntrailing outside text")
        res = convert(mk(body), "2026-11-03")
        out = res["markdown"]
        assert "undated stuff" not in out
        assert "**Inner**" in out and "inner body" in out and "after inner" in out
        assert "trailing outside text" not in out

    def test_stale_within_window_uses_latest_before_with_note(self):
        body = dated([sec_h1("2026-11-02", "two"), sec_h1("2026-10-30", "thirty")])
        res = convert(mk(body), "2026-11-09")
        assert res["status"] == "ok"
        assert res["matched_dates"] == ["2026-11-02"]
        assert res["markdown"].startswith("Notes from 2026-11-02 (no section for 2026-11-09)")
        assert "### 2026-11-02" in res["markdown"] and "thirty" not in res["markdown"]

    def test_stale_boundary_14_days_inclusive_15_excluded(self):
        body = sec_h1("2026-10-26", "old")
        assert convert(mk(body), "2026-11-09")["status"] == "ok"  # exactly 14 days
        res = convert(mk(body), "2026-11-10")  # 15 days
        assert res["status"] == "no_section" and res["markdown"] == ""
        assert convert(mk(body), "2026-11-10", max_age_days=30)["status"] == "ok"

    def test_future_only_is_no_section(self):
        res = convert(mk(sec_h1("2026-11-20", "future")), "2026-11-09")
        assert res["status"] == "no_section"
        assert res["markdown"] == ""

    def test_out_of_order_dates_use_max_not_document_order(self):
        body = dated([sec_h1("2026-11-01", "one"), sec_h1("2026-11-05", "five"),
                      sec_h1("2026-11-03", "three"), sec_h1("2026-11-12", "future")])
        res = convert(mk(body), "2026-11-09")
        assert res["matched_dates"] == ["2026-11-05"]
        assert "five" in res["markdown"] and "three" not in res["markdown"]

    def test_stale_with_duplicate_best_date_returns_both(self):
        body = dated([sec_h1("2026-11-05", "a", "X"), sec_h1("2026-11-05", "b", "Y")])
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert "### 2026-11-05 X" in out and "### 2026-11-05 Y" in out

    def test_empty_exact_section_is_no_section(self):
        body = dated([sec_h1("2026-11-09", ""), sec_h1("2026-11-02", "old")])
        assert convert(mk(body), "2026-11-09")["status"] == "no_section"

    def test_no_date_headings_converts_whole_body_with_title(self):
        res = convert(mk("just notes\n\n- a", title="Plain Page"), "2026-11-09")
        assert res["status"] == "ok" and res["matched_dates"] == []
        assert res["markdown"] == "### Plain Page\n\njust notes\n\n- a"

    def test_table_only_child_page(self):
        res = convert(load("child_table.json"), "2026-11-09")
        assert res["status"] == "ok"
        assert res["matched_dates"] == ["2026-11-09"]
        lines = res["markdown"].split("\n")
        assert lines[0] == "### 11/09 | Example Stand-Up"
        assert lines[2] == "| Workstream | Owner | 11/09 Update & Needs |"
        assert lines[3] == "| --- | --- | --- |"
        assert "Detail one<br>" in res["markdown"]
        assert "Alex Example & Sam Sample" in res["markdown"]

    def test_child_page_for_other_date_gets_stale_note(self):
        res = convert(load("child_table.json"), "2026-11-10")
        assert res["markdown"].startswith("Notes from 2026-11-09 (no section for 2026-11-10)")

    def test_running_doc_fixture_full_rendering(self):
        res = convert(load("running_doc.json"), "2026-11-09")
        out = res["markdown"]
        assert out.startswith("### 2026-11-09 EMEA/AMER\n\n##### Topics\n\n1. [Alex] <https://status.example.com/incidents/42>")
        assert "[ABC-101](https://linear.app/example-ws/issue/ABC-101/fix-the-widget)" in out
        assert "[docs.example.com](https://docs.example.com/page?a=1&b=2)" in out
        assert "    3. [ABC-12: stale head ](https://linear.app/example-ws/issue/ABC-12/x)[`main`](" in out
        assert "[Slack thread](https://example.slack.com/archives/C0AAAAAAA1/p1790000000123456)" in out
        assert "p1790000100000200?thread_ts=1790000000.123456" in out
        assert "#### Decisions" in out and "- [ ] open task" in out
        assert "> [!note] Heads up: freeze starts Friday" in out
        assert "Synced word text" in out
        assert "*italic*, ~~gone~~, ``a`b``, un**bold**ed, due 2026-12-01" in out
        assert "[Planning Doc](https://app.notion.com/p/eeee0000ffff1111aaaa2222bbbb3333)" in out
        assert "[Notion page](https://app.notion.com/p/eeee0000ffff1111aaaa2222bbbb4444)" in out
        assert "_under_ ~tilde~ {brace} $5 *star* a | pipe, `keep \\_this\\_`" in out
        assert "[image: diagram.png]" in out and "[file: report.pdf]" in out
        assert "| Area | Update \\| Notes |" in out
        assert "**Details toggle**" in out
        assert "Table of contents" not in out
        # the 2026-11-06 section and the Archive H1 are outside the 11-09 section
        assert "Older notes" not in out and "Old archive" not in out

    def test_running_doc_other_sections(self):
        res = convert(load("running_doc.json"), "2026-11-06")
        assert "Older notes" in res["markdown"] and "Monday notes" not in res["markdown"]
        res = convert(load("running_doc.json"), "2026-11-02")
        assert res["markdown"] == "### 2026-11-02\n\nMonday notes"

    def test_one_on_one_toggle_fixture(self):
        res = convert(load("one_on_one_toggle.json"), "2026-11-03")
        assert res["status"] == "ok" and res["matched_dates"] == ["2026-11-03"]
        assert res["markdown"] == ("### 2026-11-03\n\n1. Welcome!\n"
                                   "    1. ~~struck~~ text\n        1. nested")
        stale = convert(load("one_on_one_toggle.json"), "2026-11-09")
        assert stale["matched_dates"] == ["2026-11-03"]
        assert "Topic one" not in stale["markdown"]


class TestIndexPages:
    def test_exact_child_needs_child(self):
        res = convert(load("index_page.json"), "2026-11-09")
        assert res["status"] == "needs_child"
        assert res["child_candidate"] == {
            "title": "11/09 | Example Stand-Up",
            "url": "https://app.notion.com/p/cccc0000dddd1111eeee2222ffff3333"}
        assert res["matched_dates"] == ["2026-11-09"]
        assert res["markdown"] == ""

    def test_latest_child_within_window(self):
        res = convert(load("index_page.json"), "2026-11-10")
        assert res["status"] == "needs_child"
        assert res["child_candidate"]["title"].startswith("11/09")
        assert any("no child page for 2026-11-10" in w for w in res["warnings"])

    def test_future_child_not_chosen(self):
        res = convert(load("index_page.json"), "2026-11-05")
        assert res["child_candidate"]["title"].startswith("11/04")
        res = convert(load("index_page.json"), "2026-11-03")
        assert res["status"] == "no_section"

    def test_old_children_outside_window(self):
        assert convert(load("index_page.json"), "2026-11-30")["status"] == "no_section"

    def test_year_wrap(self):
        body = ('<page url="https://app.notion.com/p/c1">\n12/29 \\| Stand-Up\n</page>\n'
                '<page url="https://app.notion.com/p/c2">\n01/05 \\| Stand-Up\n</page>')
        res = convert(mk(body), "2027-01-02")
        assert res["status"] == "needs_child"
        assert res["matched_dates"] == ["2026-12-29"]
        assert res["child_candidate"]["url"].endswith("/c1")

    def test_iso_dated_children(self):
        body = '<page url="https://app.notion.com/p/c1">\n2026-11-09 Standup\n</page>'
        res = convert(mk(body), "2026-11-09")
        assert res["status"] == "needs_child"

    def test_page_with_undated_children_is_converted_whole(self):
        body = '<page url="https://app.notion.com/p/c1">\nRunbook\n</page>'
        res = convert(mk(body), "2026-11-09")
        assert res["status"] == "ok" and "[Runbook](https://app.notion.com/p/c1)" in res["markdown"]


# ---------------------------------------------------------------------------
# Size, robustness, performance
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_truncation_at_line_boundary_with_note(self):
        body = "\n".join(f"line number {i} " + "x" * 40 for i in range(400))
        res = convert(mk(body), "2026-11-09")
        out = res["markdown"]
        assert len(out) <= 6000 + len(notion_convert.TRUNCATION_NOTE) + 4
        assert out.endswith(notion_convert.TRUNCATION_NOTE)
        kept = out[:-len(notion_convert.TRUNCATION_NOTE)].rstrip().split("\n")
        assert all(l.startswith("line number") or l.startswith("###") for l in kept if l)
        assert kept[-1].endswith("x" * 40)  # no partial line

    def test_truncation_closes_open_code_fence(self):
        body = "```\n" + "\n".join("code line %d %s" % (i, "y" * 30) for i in range(400)) + "\n```"
        out = convert(mk(body), "2026-11-09")["markdown"]
        assert out.count("```") % 2 == 0

    @pytest.mark.parametrize("body", [
        "<ol><li>unclosed <b>bold <i>italic",
        "</li></ol></b></toggle></callout> stray closers",
        "<table><tr><td>a<td>b</tr></table></td>",
        "<mention url=\"user://x/y\">unclosed mention",
        "a < b and c > d & e <",
        "<b",
        "<toggle heading=\"h2\"><date start=\"2026-11-09\" />",
        '<link-preview source="',
        "<<<>>><p><<p>>",
        '# <date start="not-a-date" /> x\n<h2><date start="2026-13-45" /></h2>',
        "<callout><callout><callout>x",
        "```\nunclosed fence <b>",
        "\x01OL:0\n\x01LI:0\nbyte marker injection",
        "<code>x<code>y</code>",
        "<page url=\"u\">no close",
        "<td>cell outside table</td><tr><table>",
    ])
    def test_malformed_input_never_raises(self, body):
        for d in ("2026-11-09", None):
            res = convert(mk(body), d)
            assert res["status"] in ("ok", "no_section", "needs_child", "error")

    def test_unbalanced_tags_keep_text(self):
        out = md("<ol><li>unclosed <b>bold text")
        assert "unclosed" in out and "bold text" in out

    def test_marker_injection_neutralised(self):
        out = md("\x01TOG:1\nhello")
        assert "\x01" not in out and "hello" in out

    def test_large_page_converts_quickly(self):
        sections = []
        for i in range(54):
            day = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
            filler = "\n".join(
                f'<ol>\n<li>\n\\[Sam\\] item {j} <mention url="user://{UU}/a@example.com">Alex</mention> '
                f'<link-preview url="u" source="https://linear.app/ws/issue/ABC-{j}/x" /> & more <b>b</b>\n'
                f'1. md child {j}\n   <ol>\n   <li>\n   nested <code>c</code>\n   </li>\n   </ol>\n</li>\n</ol>'
                for j in range(14))
            sections.append(sec_h1(day, filler, "APAC/EMEA"))
        raw = mk("\n\n".join(sections))
        assert len(raw) > 100_000
        start = time.perf_counter()
        for d in ("2026-02-27", "2026-01-01", "2026-12-31", "2026-03-15"):
            res = convert(raw, d)
        elapsed = time.perf_counter() - start
        assert elapsed < 4.0, f"{elapsed:.2f}s for 4 conversions"
        assert res["status"] in ("ok", "no_section")

    def test_pathological_unclosed_tags_are_linear(self):
        body = ('<b "x ' * 20000) + ("<a href='q " * 5000) + "tail"
        start = time.perf_counter()
        convert(mk(body), "2026-11-09")
        assert time.perf_counter() - start < 3.0

    def test_many_headings_selection_is_fast(self):
        body = "\n".join(f'# <date start="2025-{1 + i % 12:02d}-{1 + i % 28:02d}" /> s\n\ntext {i}\n'
                         for i in range(3000))
        start = time.perf_counter()
        convert(mk(body), "2025-06-15")
        assert time.perf_counter() - start < 3.0


class TestCli:
    def test_cli_prints_json(self, tmp_path, capsys):
        f = tmp_path / "raw.json"
        f.write_text(load("running_doc.json"))
        assert notion_convert.main([str(f), "--date", "2026-11-02"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "ok" and data["matched_dates"] == ["2026-11-02"]


class TestNtnArtifacts:
    def test_signed_image_url_is_not_written(self):
        page = ('---\ntitle: T\n---\n\n## <mention-date start="2026-11-10"/>\n'
                '![](https://prod-files-secure.s3.us-west-2.amazonaws.com/x/image.png'
                '?X-Amz-Credential=SECRET&X-Amz-Signature=abc#notion_record=block.1)\n'
                '![logo](https://example.com/logo.png)\nBody\n')
        r = notion_convert.convert(page, '2026-11-10')
        assert r['status'] == 'ok'
        assert 'X-Amz' not in r['markdown'] and 'SECRET' not in r['markdown']
        assert 'https://example.com/logo.png' in r['markdown']

    def test_toggle_attribute_stripped_from_headings(self):
        page = ('---\ntitle: T\n---\n\n## <mention-date start="2026-11-10"/>  {toggle="true"}\n'
                'Body\n\n## NEXT {toggle="true"}\nMore\n')
        r = notion_convert.convert(page, '2026-11-10')
        assert '{toggle' not in r['markdown']
        assert r['matched_dates'] == ['2026-11-10']
