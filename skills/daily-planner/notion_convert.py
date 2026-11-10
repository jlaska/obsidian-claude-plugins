#!/usr/bin/env python3
"""
Deterministic Notion -> Obsidian markdown converter (stdlib only).

Input is the raw result of the Notion MCP ``notion-fetch`` tool (the whole JSON
object, or just its ``text`` string). Output is cleaned markdown for one dated
section (or the whole page), plus a status the caller can act on.

Public API::

    convert(raw, date, *, max_age_days=14, resolve_person=None) -> dict

Result keys: ``status`` (``ok`` | ``no_section`` | ``needs_child`` | ``error``),
``reason``, ``markdown``, ``title``, ``url``, ``matched_dates`` (list of ISO
dates), ``child_candidate`` (``{title, url}`` or None), ``warnings`` (list).

Design notes
------------
* Notion's "enhanced markdown" is NOT well-formed XML (unescaped ``&``/``<``),
  so a single tag-tokenising regex is used instead of an XML parser. Unknown or
  unmatched tags are stripped and their inner text kept; nothing here raises on
  malformed input (``convert`` turns unexpected failures into ``status=error``).
* Section selection happens on the raw body (headings come in three forms, see
  ``_find_headings``); only the chosen sections are converted.
* Output headings never use ``#``/``##``: the section title is ``###`` and every
  inner heading is ``####`` or deeper, so ``vault_utils.upsert_section`` can
  safely replace the section in place.

CLI::

    python3 notion_convert.py RAW_FILE|- [--date YYYY-MM-DD] [--max-age-days N]
"""

import argparse
import bisect
import html
import json
import re
import sys
from datetime import date as Date, timedelta
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

DEFAULT_MAX_CHARS = 6000
DEFAULT_MAX_AGE_DAYS = 14
TRUNCATION_NOTE = '_(truncated; see the Notion page for the rest)_'

ResolvePerson = Callable[[str, str], Optional[str]]

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# One tag tokeniser. An escaped ``\<`` is literal text. Attribute values are
# bounded to one line so a stray quote cannot cause quadratic scanning.
_TAG_RE = re.compile(
    r'(?<!\\)<(/?)([A-Za-z][A-Za-z0-9-]*)((?:[^<>"\']|"[^"\n]*"|\'[^\'\n]*\')*?)(/?)>'
)
_ATTR_RE = re.compile(r'([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
_FENCE_RE = re.compile(r'^```[^\n]*\n.*?^```[ \t]*$', re.M | re.S)

# Text-level cleanup: protected spans (code, link destinations) are matched
# first and returned unchanged; escapes and emoji shortcodes are rewritten.
_TEXT_RE = re.compile(
    r'(?P<code>``(?:[^`\n]|`(?!`))*``|`[^`\n]*`)'
    r'|(?P<dest>\]\([^)\n]*\))'
    r'|\\(?P<esc>[\[\]_~<>|{}$*])'
    r'|(?<![\w:])(?P<emoji>:[a-z][a-z0-9_+-]*:)(?![\w:]) ?'
)
_SLACK_RE = re.compile(
    r'slackMessage://([\w.-]+)/([A-Za-z0-9]+)/(\d+)\.(\d+)(?:/(\d+)\.(\d+))?'
)
_SLACK_LINK_RE = re.compile(r'\[([^\]\n]*)\]\((slackMessage://[^)\s]+)\)')
_LINK_RE = re.compile(r'\[([^\]\n]+)\]\(([^)\s]+)\)')
_LINEAR_RE = re.compile(r'linear\.app/[^/]+/issue/([A-Za-z0-9]+-\d+)')

_LIST_RE = re.compile(r'^([ \t]*)([-*+]|\d+[.)])(?:[ \t]+(.*))?$')
_HEAD_RE = re.compile(r'^(#{1,6})[ \t]+(\S.*)$')
_HR_RE = re.compile(r'^\s*([-*_])(?:\s*\1){2,}\s*$')

_DATE_VAL = r'(\d{4}-\d{2}-\d{2})'
_DATE_TAG = r'<date\s+start="' + _DATE_VAL + r'[^"]*"[^>]*?/?>'
_H1_DATE_RE = re.compile(r'^#[ \t]*' + _DATE_TAG + r'[ \t]*([^\n]*)$', re.M)
_HBLOCK_DATE_RE = re.compile(r'<h([12])>\s*' + _DATE_TAG + r'\s*([^<]*?)\s*</h\1>')
_TOGGLE_DATE_RE = re.compile(
    r'<toggle\b[^>]*\bheading="h[1-4]"[^>]*>\s*' + _DATE_TAG + r'([^\n]*)'
)
_H1_PLAIN_RE = re.compile(r'^#[ \t]+(?=\S)', re.M)
_H1_TAG_RE = re.compile(r'<h1>(?!\s*</h1>)')
_TOGGLE_TOK_RE = re.compile(r'<toggle\b[^>]*?(/?)>|</toggle\s*>')
_PAGE_RE = re.compile(r'<page\s+url="([^"]*)"[^>]*>([^<]*)</page>')

_INLINE_WRAP = {
    'b': '**', 'strong': '**', 'i': '*', 'em': '*', 's': '~~', 'del': '~~', 'strike': '~~',
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _attrs(attr_str: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_str or ''):
        val = m.group(2) if m.group(2) is not None else m.group(3)
        out[m.group(1).lower()] = html.unescape(val or '')
    return out


def _unescape_md(text: str) -> str:
    return re.sub(r'\\([\[\]_~<>|{}$*])', r'\1', text)


def _clean_title(raw: str) -> str:
    t = re.sub(r'<[^>]*>', '', raw or '')
    t = _unescape_md(t)
    t = html.unescape(t)
    return re.sub(r'\s+', ' ', t).strip()


def _slack_url(m: 're.Match') -> str:
    ws, chan, a, b, c, d = m.groups()
    url = f'https://{ws}/archives/{chan}/p{a}{b}'
    if c and (c, d) != (a, b):
        url += f'?thread_ts={c}.{d}'
    return url


def _slack_link(m: 're.Match') -> str:
    label, raw = m.group(1), m.group(2)
    if label.strip() == raw:
        label = 'Slack thread'
    return f'[{label}]({_SLACK_RE.sub(_slack_url, raw)})'


def _clean_text(s: str) -> str:
    """Slack links, markdown unescapes and emoji shortcodes on a text token."""
    if 'slackMessage://' in s:
        s = _SLACK_LINK_RE.sub(_slack_link, s)
        s = _SLACK_RE.sub(_slack_url, s)

    def repl(m: 're.Match') -> str:
        if m.group('esc'):
            return m.group('esc')
        if m.group('emoji'):
            return ''
        return m.group(0)

    return _TEXT_RE.sub(repl, s)


def _collapse_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def _link_text_for(source: str) -> str:
    m = _LINEAR_RE.search(source)
    if m:
        return m.group(1)
    return urlparse(source).netloc or source


def _asset_name(source: str) -> str:
    q = parse_qs(urlparse(source).query)
    names = q.get('name')
    return names[0] if names else ''


def _demote(level: int) -> str:
    """Map a source heading level to the output heading (never # or ##)."""
    return '#' * {1: 4, 2: 4, 3: 5}.get(level, 6)


def _parse_date(s: str) -> Optional[Date]:
    try:
        return Date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Tag-stream -> intermediate text with marker lines
# ---------------------------------------------------------------------------

class _Converter:
    """Walks the tag stream and produces text with ``\\x01`` marker lines for
    block structure (lists, toggles, callouts, tables, fences). ``_render``
    turns that into final markdown lines."""

    def __init__(self, resolve_person: Optional[ResolvePerson], warnings: List[str]):
        self.out: List[str] = []
        self.frames: List[list] = []  # [name, out_index, attrs]
        self.tables: List[dict] = []
        self.fences: List[str] = []
        self.resolve_person = resolve_person
        self.warnings = warnings
        self._person_cache: Dict[Tuple[str, str], Optional[str]] = {}
        self.body = ''

    # -- state helpers ------------------------------------------------------
    def _in_cell(self) -> bool:
        return any(f[0] in ('td', 'th') for f in self.frames)

    def _in_code(self) -> bool:
        return any(f[0] == 'code' for f in self.frames)

    def _struct(self, kind: str, arg: str = '') -> None:
        if self._in_cell():
            return
        self.out.append(f'\n\x01{kind}:{arg}\n')

    def _open(self, name: str, attrs: Dict[str, str]) -> None:
        self.frames.append([name, len(self.out), attrs])

    def _close(self, name: str):
        for i in range(len(self.frames) - 1, -1, -1):
            if self.frames[i][0] == name:
                break
        else:
            return None
        frame = self.frames[i]
        del self.frames[i:]
        inner = ''.join(self.out[frame[1]:])
        del self.out[frame[1]:]
        return frame, inner

    # -- feeding ------------------------------------------------------------
    def feed(self, text: str) -> None:
        pos = 0
        self.body = text
        for m in _TAG_RE.finditer(text):
            if m.start() > pos:
                self._text(text[pos:m.start()])
            pos = m.end()
            self._tag(m)
        if pos < len(text):
            self._text(text[pos:])

    def add_fence(self, fence: str) -> None:
        self.fences.append(fence)
        self.out.append(f'\n\x01F:{len(self.fences) - 1}\n')

    def _text(self, s: str) -> None:
        if not self._in_code():
            s = _clean_text(s)
        self.out.append(s)

    def finish(self) -> str:
        return ''.join(self.out)

    # -- tags ---------------------------------------------------------------
    def _tag(self, m: 're.Match') -> None:
        closing, name, attr_str, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if closing:
            self._end_tag(name)
            return
        attrs = _attrs(attr_str)
        if name in ('ol', 'ul', 'li'):
            if not self._in_cell():
                ls = self.body.rfind('\n', 0, m.start()) + 1
                prefix = self.body[ls:m.start()]
                indent = len(prefix.expandtabs(4)) if not prefix.strip() else 0
                self.out.append(f'\n\x01{name.upper()}:{indent}\n')
        elif name == 'br':
            self.out.append('<br>' if self._in_cell() else '\n')
        elif name == 'p':
            if not selfclose:
                self.out.append('\n')
        elif name == 'date':
            start = attrs.get('start', '')
            end = attrs.get('end', '')
            self.out.append(f'{start} → {end}' if end else start)
        elif name == 'mention':
            if selfclose:
                self.out.append(self._mention(attrs, ''))
            else:
                self._open('mention', attrs)
        elif name == 'link-preview':
            source = attrs.get('source', '')
            if source:
                self.out.append(f'[{_link_text_for(source)}]({source})')
        elif name in ('image', 'file', 'pdf', 'audio', 'video'):
            label = 'image' if name == 'image' else 'file'
            fname = _asset_name(attrs.get('source', '') or attrs.get('url', ''))
            self.out.append(f'[{label}: {fname}]' if fname else f'[{label}]')
        elif name in _INLINE_WRAP or name in ('code', 'a', 'page') or re.fullmatch(r'h[1-6]', name):
            if selfclose:
                if name == 'page':
                    self.out.append(self._page_entry(attrs, ''))
            else:
                self._open(name, attrs)
        elif name in ('td', 'th'):
            if not selfclose:
                self._open(name, attrs)
        elif name == 'tr':
            if self.tables:
                t = self.tables[-1]
                if t['row'] is not None:
                    t['rows'].append(t['row'])
                t['row'] = []
        elif name == 'table':
            self.tables.append({'rows': [], 'row': None})
        elif name == 'callout':
            self._struct('CALL')
        elif name == 'toggle':
            if not selfclose:
                heading = attrs.get('heading', '')
                self._struct('TOG', heading[1:] if re.fullmatch(r'h[1-6]', heading) else '')
        # everything else (unknown, table-of-contents, embed, span, synced-block,
        # column, ...) is dropped; inner text of non-void tags is kept.

    def _end_tag(self, name: str) -> None:
        if name in ('ol', 'ul'):
            self._struct('END')
        elif name == 'li':
            self._struct('ENDLI')
        elif name == 'p':
            self.out.append('\n')
        elif name == 'callout':
            self._struct('ENDCALL')
        elif name == 'toggle':
            self._struct('ENDTOG')
        elif name in _INLINE_WRAP:
            self._end_wrap(name)
        elif name == 'code':
            res = self._close('code')
            if res:
                inner = res[1]
                if inner.strip():
                    tick = '``' if '`' in inner else '`'
                    pad = ' ' if inner.startswith('`') or inner.endswith('`') else ''
                    self.out.append(f'{tick}{pad}{inner}{pad}{tick}')
        elif name == 'a':
            res = self._close('a')
            if res:
                href = res[0][2].get('href', '')
                inner = res[1].strip()
                self.out.append(f'[{inner}]({href})' if href and inner else inner)
        elif name == 'mention':
            res = self._close('mention')
            if res:
                self.out.append(self._mention(res[0][2], res[1]))
        elif name == 'page':
            res = self._close('page')
            if res:
                self.out.append(self._page_entry(res[0][2], res[1]))
        elif re.fullmatch(r'h[1-6]', name):
            res = self._close(name)
            if res:
                text = _collapse_ws(res[1])
                if text and not self._in_cell():
                    self.out.append(f'\n{"#" * int(name[1])} {text}\n')
                elif text:
                    self.out.append(text)
        elif name in ('td', 'th'):
            res = self._close(name)
            if res:
                cell = res[1].replace('\x01', '')
                cell = re.sub(r'\s*\n\s*', ' ', cell)
                cell = re.sub(r'[ \t]+', ' ', cell).strip().replace('|', '\\|')
                if self.tables:
                    t = self.tables[-1]
                    if t['row'] is None:
                        t['row'] = []
                    t['row'].append(cell)
        elif name == 'tr':
            if self.tables and self.tables[-1]['row'] is not None:
                t = self.tables[-1]
                t['rows'].append(t['row'])
                t['row'] = None
        elif name == 'table':
            if self.tables:
                self._flush_table(self.tables.pop())
        # other closing tags: ignored

    def _end_wrap(self, name: str) -> None:
        res = self._close(name)
        if not res:
            return
        marker = _INLINE_WRAP[name]
        inner = res[1]
        if not inner.strip():
            self.out.append(inner)
            return
        if '\n' in inner or '\x01' in inner:
            self.out.append(f'{marker}{inner}{marker}')
            return
        core = inner.strip()
        lead = inner[:len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        self.out.append(f'{lead}{marker}{core}{marker}{trail}')

    def _mention(self, attrs: Dict[str, str], inner: str) -> str:
        url = attrs.get('url', '')
        name = _collapse_ws(inner)
        if url.startswith('user://'):
            rest = url[len('user://'):]
            email = rest.split('/', 1)[1] if '/' in rest else ''
            link = self._resolve(email, name)
            return link or name or email or 'user'
        if url:
            label = (name or 'Notion page').replace(']', '\\]')
            return f'[{label}]({url})'
        return name

    def _resolve(self, email: str, name: str) -> Optional[str]:
        if not self.resolve_person or not (email or name):
            return None
        key = (email, name)
        if key not in self._person_cache:
            try:
                res = self.resolve_person(email, name)
                self._person_cache[key] = res if isinstance(res, str) and res.strip() else None
            except Exception as exc:  # resolver is best-effort
                self.warnings.append(f'resolve_person failed for {name or email}: {exc}')
                self._person_cache[key] = None
        return self._person_cache[key]

    def _page_entry(self, attrs: Dict[str, str], inner: str) -> str:
        url = attrs.get('url', '')
        title = _collapse_ws(inner) or 'Notion page'
        if not url:
            return f'\n- {title}\n'
        label = title.replace(']', '\\]')
        return f'\n- [{label}]({url})\n'

    def _flush_table(self, table: dict) -> None:
        rows = table['rows']
        if table['row'] is not None:
            rows.append(table['row'])
        rows = [r for r in rows if r is not None]
        if not rows:
            return
        if self._in_cell():
            self.out.append(' / '.join(' '.join(r) for r in rows))
            return
        ncols = max(len(r) for r in rows) or 1
        lines = []
        for i, r in enumerate(rows):
            r = list(r) + [''] * (ncols - len(r))
            lines.append(('T1' if i == 0 else 'T') + ':| ' + ' | '.join(r) + ' |')
            if i == 0:
                lines.append('T:|' + ' --- |' * ncols)
        self.out.append('\n' + ''.join(f'\x01{ln}\n' for ln in lines))


# ---------------------------------------------------------------------------
# Marker text -> final markdown lines (list nesting, toggles, callouts)
# ---------------------------------------------------------------------------

def _render(s: str, fences: List[str]) -> str:
    lines: List[str] = []
    ctx: List[dict] = []
    st = {'blank': False, 'prev': None, 'callout': 0, 'first': False, 'suppress': None}
    toggles: List[dict] = []

    def child_level(f: dict) -> int:
        if f['k'] == 'li':
            return f['level'] + 1 if f['emitted'] else f['level']
        return f['level'] + 1

    def base() -> int:
        return child_level(ctx[-1]) if ctx else 0

    def emit(text: str, kind: str) -> None:
        text = text.rstrip()
        if st['callout']:
            if st['first']:
                st['first'] = False
                if kind == 'text':
                    lines.append('> [!note] ' + text)
                else:
                    lines.append('> [!note]')
                    lines.append('> ' + text)
            else:
                lines.append('> ' + text)
            st['prev'] = 'quote'
            st['blank'] = False
            return
        prev = st['prev']
        if lines and lines[-1] != '':
            blank = False
            if prev == 'quote':
                blank = True
            elif kind == 'head' or prev == 'head':
                blank = True
            elif kind in ('table', 'table1', 'fence') or prev in ('table', 'fence'):
                blank = kind != prev or kind == 'table1'
            elif kind == 'list':
                blank = prev != 'list'
            else:  # text
                blank = prev == 'list' or st['blank']
            if blank:
                lines.append('')
        lines.append(text)
        st['prev'] = 'table' if kind == 'table1' else kind
        st['blank'] = False

    def pop_md_while(pred) -> None:
        while ctx and ctx[-1]['k'] == 'md' and pred(ctx[-1]):
            ctx.pop()

    for line in s.split('\n'):
        if line.startswith('\x01'):
            name, _, arg = line[1:].partition(':')
            if st['suppress'] is not None and name not in ('TOG', 'ENDTOG'):
                continue
            if name in ('OL', 'UL'):
                if toggles:
                    toggles[-1]['pending'] = False
                ind = int(arg or 0)
                pop_md_while(lambda f: f['src'] >= ind)
                ctx.append({'k': 'html', 'type': name.lower(), 'level': base(), 'counter': 0})
            elif name == 'LI':
                if toggles:
                    toggles[-1]['pending'] = False
                while ctx and ctx[-1]['k'] in ('md', 'li'):
                    ctx.pop()
                if not ctx or ctx[-1]['k'] != 'html':
                    ctx.append({'k': 'html', 'type': 'ul', 'level': base(), 'counter': 0})
                parent = ctx[-1]
                ctx.append({'k': 'li', 'level': parent['level'], 'emitted': False, 'parent': parent})
            elif name == 'ENDLI':
                for i in range(len(ctx) - 1, -1, -1):
                    if ctx[i]['k'] == 'li':
                        del ctx[i:]
                        break
            elif name == 'END':
                for i in range(len(ctx) - 1, -1, -1):
                    if ctx[i]['k'] == 'html':
                        del ctx[i:]
                        break
            elif name == 'TOG':
                toggles.append({'pending': True, 'h': arg})
            elif name == 'ENDTOG':
                if toggles:
                    toggles.pop()
                if st['suppress'] is not None and len(toggles) < st['suppress']:
                    st['suppress'] = None
            elif name == 'CALL':
                if lines and lines[-1] != '':
                    lines.append('')
                st['callout'] += 1
                st['first'] = True
            elif name == 'ENDCALL':
                st['callout'] = max(0, st['callout'] - 1)
                st['first'] = False
                st['prev'] = 'quote'
            elif name == 'F':
                fence = fences[int(arg)]
                emit(fence.split('\n', 1)[0], 'fence')
                for fl in fence.split('\n')[1:]:
                    lines.append(('> ' if st['callout'] else '') + fl)
            elif name in ('T', 'T1'):
                if toggles:
                    toggles[-1]['pending'] = False
                emit('    ' * base() + arg if ctx else arg, 'table1' if name == 'T1' else 'table')
            continue

        if st['suppress'] is not None:
            continue
        text = line.strip()
        if not text:
            st['blank'] = True
            continue
        if _HR_RE.match(text):
            continue
        indent = len(line[:len(line) - len(line.lstrip())].expandtabs(4))
        list_m = _LIST_RE.match(line)

        # Toggle title (first text line inside a toggle)
        if toggles and toggles[-1]['pending']:
            if list_m:
                toggles[-1]['pending'] = False
            else:
                tog = toggles[-1]
                tog['pending'] = False
                if text.lower() == 'table of contents':
                    st['suppress'] = len(toggles)
                    continue
                if tog['h']:
                    text = '#' * int(tog['h']) + ' ' + text
                elif not (text.startswith('**') and text.endswith('**')):
                    text = f'**{text}**'

        head_m = _HEAD_RE.match(text)
        if head_m and not list_m:
            pop_md_while(lambda f: True)
            emit(f'{_demote(len(head_m.group(1)))} {head_m.group(2)}', 'head')
            continue

        if list_m:
            body = (list_m.group(3) or '').strip()
            if not body or body in ('[ ]', '[x]', '[X]'):
                continue
            marker = list_m.group(2)
            typ = 'ol' if marker[0].isdigit() else 'ul'
            pop_md_while(lambda f: f['src'] > indent)
            top = ctx[-1] if ctx else None
            if top and top['k'] == 'md' and top['src'] == indent:
                if top['type'] == typ:
                    top['counter'] += 1
                else:
                    top['type'], top['counter'] = typ, 1
                frame = top
            else:
                frame = {'k': 'md', 'src': indent, 'type': typ, 'counter': 1, 'level': base()}
                ctx.append(frame)
            mk = f'{frame["counter"]}.' if typ == 'ol' else '-'
            emit('    ' * frame['level'] + f'{mk} {body}', 'list')
            continue

        pop_md_while(lambda f: indent <= f['src'])
        top = ctx[-1] if ctx else None
        if top is None:
            emit(text, 'text')
        elif top['k'] == 'li' and not top['emitted']:
            parent = top['parent']
            parent['counter'] += 1
            mk = f'{parent["counter"]}.' if parent['type'] == 'ol' else '-'
            top['emitted'] = True
            emit('    ' * top['level'] + f'{mk} {text}', 'list')
        else:
            emit('    ' * child_level(top) + text, 'list')

    while lines and lines[0] == '':
        lines.pop(0)
    while lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines)


def _tidy(md: str) -> str:
    """Final text-level fixes: collapse ``[https://x](https://x)`` to ``<https://x>``."""
    def repl(m: 're.Match') -> str:
        label, url = m.group(1), m.group(2)
        return f'<{url}>' if label == url and re.match(r'https?://', url) else m.group(0)
    return _LINK_RE.sub(repl, md)


def _convert_markup(text: str, resolve_person: Optional[ResolvePerson],
                    warnings: List[str]) -> str:
    text = text.replace('\x01', '')
    conv = _Converter(resolve_person, warnings)
    pos = 0
    for fm in _FENCE_RE.finditer(text):
        conv.feed(text[pos:fm.start()])
        conv.add_fence(fm.group(0))
        pos = fm.end()
    conv.feed(text[pos:])
    return _tidy(_render(conv.finish(), conv.fences))


def _inline_markup(text: str, resolve_person: Optional[ResolvePerson],
                   warnings: List[str]) -> str:
    return _collapse_ws(_convert_markup(text, resolve_person, warnings))


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

def _unwrap(raw) -> Tuple[Optional[str], dict, str]:
    """Return (text, meta, error_reason). ``meta`` has title/url when known."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode('utf-8', 'replace')
    obj = None
    if isinstance(raw, (dict, list)):
        obj = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, {}, 'empty input'
        if s[0] in '{[':
            try:
                obj = json.loads(s)
            except ValueError:
                obj = None
        if obj is None:
            return s, {}, ''
    else:
        return None, {}, f'unsupported input type {type(raw).__name__}'

    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                if isinstance(item.get('text'), str) and item.get('type') == 'text':
                    return _unwrap(item['text'])
                return _unwrap(item)
        return None, {}, 'unrecognised JSON list'
    if isinstance(obj, dict):
        if isinstance(obj.get('text'), str):
            return obj['text'], {'title': obj.get('title') or '', 'url': obj.get('url') or ''}, ''
        if any(k in obj for k in ('code', 'message', 'error', 'name')):
            bits = [str(obj.get(k)) for k in ('name', 'code') if obj.get(k)]
            msg = obj.get('message') or obj.get('error') or ''
            reason = ' '.join(bits)
            if msg:
                reason = f'{reason}: {msg}' if reason else str(msg)
            return None, {}, reason or 'error response'
        return None, {}, 'unrecognised JSON shape (no text field)'
    return None, {}, 'unrecognised input'


def _extract_body(text: str, warnings: List[str]) -> Optional[str]:
    start = text.find('<content>')
    if start == -1:
        return None
    start += len('<content>')
    end = text.rfind('</content>')
    if end < start:
        warnings.append('missing </content>; using the rest of the text')
        end = len(text)
    return text[start:end]


def _title_from_properties(text: str) -> str:
    m = re.search(r'<properties>(.*?)</properties>', text, re.S)
    if not m:
        return ''
    blob = m.group(1).strip()
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            return _clean_title(str(data.get('title', '')))
    except ValueError:
        pass
    tm = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', blob)
    return _clean_title(tm.group(1)) if tm else ''


# ---------------------------------------------------------------------------
# Dated-section selection
# ---------------------------------------------------------------------------

def _fence_spans(body: str) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in _FENCE_RE.finditer(body)]


def _in_spans(pos: int, spans: List[Tuple[int, int]], starts: List[int]) -> bool:
    i = bisect.bisect_right(starts, pos) - 1
    return i >= 0 and spans[i][0] <= pos < spans[i][1]


def _find_headings(body: str) -> List[dict]:
    """Find date headings (three forms) and compute each section's extent."""
    spans = _fence_spans(body)
    starts = [a for a, _ in spans]
    heads: List[dict] = []
    nondate: set = set()

    for m in _H1_DATE_RE.finditer(body):
        if _in_spans(m.start(), spans, starts):
            continue
        heads.append({'start': m.start(), 'cstart': m.end(), 'date': m.group(1),
                      'suffix': m.group(2), 'kind': 'h1'})
    for m in _HBLOCK_DATE_RE.finditer(body):
        if _in_spans(m.start(), spans, starts):
            continue
        heads.append({'start': m.start(), 'cstart': m.end(), 'date': m.group(2),
                      'suffix': m.group(3), 'kind': 'hblock'})
    for m in _TOGGLE_DATE_RE.finditer(body):
        if _in_spans(m.start(), spans, starts):
            continue
        heads.append({'start': m.start(), 'cstart': m.end(), 'date': m.group(1),
                      'suffix': m.group(2), 'kind': 'toggle'})

    date_starts = {h['start'] for h in heads}
    for m in _H1_PLAIN_RE.finditer(body):
        if m.start() not in date_starts and not _in_spans(m.start(), spans, starts):
            nondate.add(m.start())
    for m in _H1_TAG_RE.finditer(body):
        if m.start() not in date_starts and not _in_spans(m.start(), spans, starts):
            nondate.add(m.start())

    heads = [h for h in heads if _parse_date(h['date'])]
    if not heads:
        return []
    heads.sort(key=lambda h: h['start'])

    # Matching </toggle> for each opening toggle (linear stack walk).
    close_of: Dict[int, int] = {}
    stack: List[int] = []
    for m in _TOGGLE_TOK_RE.finditer(body):
        if m.group(0).startswith('</'):
            if stack:
                close_of[stack.pop()] = m.start()
        elif not m.group(1):
            stack.append(m.start())

    bounds = sorted(date_starts | nondate)
    for h in heads:
        if h['kind'] == 'toggle':
            h['end'] = close_of.get(h['start'], len(body))
        else:
            i = bisect.bisect_right(bounds, h['start'])
            h['end'] = bounds[i] if i < len(bounds) else len(body)
        h['end'] = max(h['end'], h['cstart'])
    return heads


def _child_pages(body: str) -> List[dict]:
    return [{'url': m.group(1), 'title': _clean_title(m.group(2))}
            for m in _PAGE_RE.finditer(body)]


def _title_date(title: str, target: Date) -> Optional[Date]:
    """Date in a child-page title (``11/09 | ...`` or ``2026-11-09 ...``)."""
    m = re.match(r'\s*(\d{4})-(\d{2})-(\d{2})(?!\d)', title)
    if m:
        return _parse_date('-'.join(m.groups()))
    m = re.match(r'\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?!\d)', title)
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if yy:
            year = int(yy) + (2000 if int(yy) < 100 else 0)
            return Date(year, mm, dd)
        for year in (target.year, target.year - 1):
            try:
                d = Date(year, mm, dd)
            except ValueError:
                continue
            if d <= target:
                return d
        return None
    except ValueError:
        return None


def _cap(md: str, max_chars: int) -> str:
    if max_chars and len(md) > max_chars:
        cut = md.rfind('\n', 0, max_chars)
        cut = cut if cut > 0 else max_chars
        md = md[:cut].rstrip()
        if md.count('```') % 2:
            md += '\n```'
        md += '\n\n' + TRUNCATION_NOTE
    return md


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert(raw, date: Optional[str] = None, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
            resolve_person: Optional[ResolvePerson] = None,
            max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """Convert raw ``notion-fetch`` output into markdown for ``date``."""
    result = {'status': 'error', 'reason': '', 'markdown': '', 'title': '', 'url': '',
              'matched_dates': [], 'child_candidate': None, 'warnings': []}
    try:
        _convert(result, raw, date, max_age_days, resolve_person, max_chars)
    except Exception as exc:  # never raise on malformed input
        result.update(status='error', reason=f'internal error: {type(exc).__name__}: {exc}',
                      markdown='', matched_dates=[], child_candidate=None)
    return result


def _convert(result: dict, raw, date: Optional[str], max_age_days: int,
             resolve_person: Optional[ResolvePerson], max_chars: int) -> None:
    warnings: List[str] = result['warnings']
    target = None
    if date:
        target = _parse_date(date)
        if target is None:
            result['reason'] = f'invalid date {date!r} (expected YYYY-MM-DD)'
            return

    text, meta, err = _unwrap(raw)
    if text is None:
        result['reason'] = err
        return
    result['url'] = meta.get('url', '')
    body = _extract_body(text, warnings)
    if body is None:
        result['reason'] = 'unrecognised Notion output: no <content> block'
        return
    title = _clean_title(meta.get('title', '')) or _title_from_properties(text)
    result['title'] = title
    body = body.replace('\x01', '')

    def finish(parts: List[str], dates: List[str]) -> None:
        md = _cap('\n\n'.join(p for p in parts if p), max_chars)
        result.update(status='ok', markdown=md, matched_dates=dates)

    heads = _find_headings(body)
    if heads:
        if target is None:
            target_eff = max(_parse_date(h['date']) for h in heads)
        else:
            target_eff = target
        exact = [h for h in heads if h['date'] == target_eff.isoformat()]
        stale_date = None
        if exact:
            chosen = exact
        else:
            before = [_parse_date(h['date']) for h in heads if _parse_date(h['date']) < target_eff]
            if not before:
                result.update(status='no_section',
                              reason=f'no section on or before {target_eff.isoformat()} '
                                     '(only later dates)')
                return
            best = max(before)
            if (target_eff - best) > timedelta(days=max_age_days):
                result.update(status='no_section',
                              reason=f'most recent section ({best.isoformat()}) is older than '
                                     f'{max_age_days} days')
                return
            stale_date = best.isoformat()
            chosen = [h for h in heads if h['date'] == stale_date]
        parts: List[str] = []
        if stale_date:
            parts.append(f'Notes from {stale_date} (no section for {target_eff.isoformat()})')
        any_content = False
        for h in chosen:
            md = _convert_markup(body[h['cstart']:h['end']], resolve_person, warnings)
            any_content = any_content or bool(md.strip())
            suffix = _inline_markup(h['suffix'], resolve_person, warnings)
            head = f'### {h["date"]}' + (f' {suffix}' if suffix else '')
            parts.append(head + (f'\n\n{md}' if md else ''))
        if not any_content:
            result.update(status='no_section',
                          reason=f'section for {chosen[0]["date"]} is empty')
            return
        finish(parts, [chosen[0]['date']])
        return

    # No dated headings: index page of dated children, or a plain page.
    children = _child_pages(body)
    dated_children = []
    if target is not None:
        for c in children:
            d = _title_date(c['title'], target)
            if d:
                dated_children.append((d, c))
    if dated_children:
        exact = [(d, c) for d, c in dated_children if d == target]
        if exact:
            d, c = exact[0]
            if len(exact) > 1:
                warnings.append(f'{len(exact)} child pages match {target.isoformat()}; using the first')
        else:
            older = [(d, c) for d, c in dated_children if d < target]
            if not older:
                result.update(status='no_section',
                              reason='no child page on or before the date (only later dates)')
                return
            d = max(x for x, _ in older)
            if (target - d) > timedelta(days=max_age_days):
                result.update(status='no_section',
                              reason=f'most recent child page ({d.isoformat()}) is older than '
                                     f'{max_age_days} days')
                return
            c = next(c for x, c in older if x == d)
            warnings.append(f'no child page for {target.isoformat()}; using {d.isoformat()}')
        result.update(status='needs_child', reason='index page; fetch the child page',
                      matched_dates=[d.isoformat()],
                      child_candidate={'title': c['title'], 'url': c['url']})
        return

    md = _convert_markup(body, resolve_person, warnings)
    if not md.strip():
        result.update(status='no_section', reason='page has no content')
        return
    parts = []
    dates: List[str] = []
    if target is not None and re.match(r'\s*\d{1,2}/\d{1,2}\s*[|\-–:]', title):
        td = _title_date(title, target)
        if td:
            dates = [td.isoformat()]
            if td != target:
                parts.append(f'Notes from {td.isoformat()} (no section for {target.isoformat()})')
    if title:
        parts.append(f'### {title}')
    parts.append(md)
    finish(parts, dates)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Convert raw notion-fetch output to markdown')
    parser.add_argument('raw_file', help='file with raw notion-fetch output, or - for stdin')
    parser.add_argument('--date', help='target date YYYY-MM-DD')
    parser.add_argument('--max-age-days', type=int, default=DEFAULT_MAX_AGE_DAYS)
    args = parser.parse_args(argv)
    raw = sys.stdin.read() if args.raw_file == '-' else open(args.raw_file, encoding='utf-8').read()
    print(json.dumps(convert(raw, args.date, max_age_days=args.max_age_days),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
