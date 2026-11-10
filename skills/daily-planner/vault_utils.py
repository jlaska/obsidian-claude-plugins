#!/usr/bin/env python3
"""
Shared utilities for Obsidian vault operations.

Functions used by multiple daily-planner scripts (sync_to_vault.py,
gather_meeting_context.py, etc.).
"""

import html
import json
import re
from urllib.parse import unquote, urlparse, parse_qs
from pathlib import Path
from typing import Dict, List, Optional


def sanitize_title(title: str) -> str:
    """Sanitize meeting title for use as a filesystem filename."""
    title = title.replace('/', ' - ')
    title = title.replace(':', ' - ')
    title = title.replace('|', ' - ')
    title = re.sub(r'[<>"\\|?*]', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title.strip()


def html_to_markdown(html_content: str) -> str:
    """Convert HTML content to Obsidian-compatible markdown."""
    html_content = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
        r'[\2](\1)',
        html_content,
        flags=re.IGNORECASE
    )
    html_content = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'<[^>]+>', '', html_content)
    html_content = html_content.replace('&amp;', '&')
    html_content = html_content.replace('&lt;', '<')
    html_content = html_content.replace('&gt;', '>')
    html_content = html_content.replace('&nbsp;', ' ')
    html_content = html_content.replace('&quot;', '"')
    return html_content.strip()


def extract_doc_id_from_url(url: str) -> Optional[str]:
    """Extract Google Doc ID from a Google Docs URL."""
    match = re.search(r'/document/d/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None


_NOTION_URL_RE = re.compile(
    r'https?://(?:www\.|app\.)?notion\.(?:so|com|site)/[^\s"\'<>]+', re.IGNORECASE
)
_NOTION_ID_RE = re.compile(r'([0-9a-f]{32})(?![0-9a-f])', re.IGNORECASE)


def extract_notion_links(text: str) -> List[Dict[str, object]]:
    """Find Notion page/database links in a calendar event description.

    Handles bare URLs, <a href> anchors, HTML-escaped ``&amp;``, and
    ``google.com/url?q=`` redirect wrappers. Tracking parameters are dropped;
    only the ``v`` (database view) parameter is kept.

    Returns a de-duplicated list of ``{url, page_id, is_database}`` dicts,
    pages first and databases (URLs with ``?v=``) last, in order of appearance.
    """
    if not text:
        return []
    text = html.unescape(text)
    found: List[Dict[str, object]] = []
    seen = set()
    for match in _NOTION_URL_RE.finditer(text):
        raw = match.group(0)
        # Inside a google.com/url?q= wrapper the Notion URL is followed by the
        # wrapper's own parameters (&sa=..., &source=calendar, &ust=, &usg=).
        raw = re.split(r'&(?:sa|source=calendar|ust|usg)=', raw, maxsplit=1)[0]
        raw = unquote(raw).rstrip('.,;)')
        parsed = urlparse(raw)
        path = parsed.path.rstrip('/')
        id_match = _NOTION_ID_RE.search(path.replace('-', '')) if path else None
        # Prefer the trailing 32-hex id on the last path segment.
        last = path.rsplit('/', 1)[-1].replace('-', '')
        tail = _NOTION_ID_RE.findall(last)
        page_id = (tail[-1] if tail else (id_match.group(1) if id_match else '')).lower()
        view = parse_qs(parsed.query).get('v', [''])[0]
        if not page_id:
            continue
        key = (page_id, view)
        if key in seen:
            continue
        seen.add(key)
        url = f'{parsed.scheme}://{parsed.netloc}{path}'
        if view:
            url += f'?v={view}'
        found.append({'url': url, 'page_id': page_id, 'is_database': bool(view)})
    found.sort(key=lambda d: d['is_database'])  # stable: pages first
    return found


def parse_frontmatter(content: str) -> Dict[str, str]:
    """Extract YAML frontmatter key-value pairs from file content.

    Returns a flat dict of raw string values (lists are returned as the
    raw YAML string, not parsed). For structured access, use a YAML parser.
    """
    if not content.startswith('---'):
        return {}
    end = content.find('---', 3)
    if end == -1:
        return {}
    fm_text = content[3:end]
    result: Dict[str, str] = {}
    for line in fm_text.split('\n'):
        if line and not line.startswith(' ') and ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip()
            if key:
                result[key] = val
    return result


def update_frontmatter_with_missing_properties(content: str, new_props: Dict[str, str]) -> str:
    """Add missing frontmatter properties. Never overwrites existing values."""
    if not content.startswith('---'):
        return content
    end = content.find('---', 3)
    if end == -1:
        return content
    frontmatter_text = content[3:end]
    existing_keys = set()
    for line in frontmatter_text.split('\n'):
        if line and not line.startswith(' ') and ':' in line:
            key = line.split(':', 1)[0].strip()
            if key:
                existing_keys.add(key)
    additions = [f'{k}: {v}' for k, v in new_props.items() if k not in existing_keys and v]
    if not additions:
        return content
    new_frontmatter = frontmatter_text.rstrip() + '\n' + '\n'.join(additions) + '\n'
    return '---' + new_frontmatter + '---' + content[end + 3:]


def update_frontmatter_values(content: str, updates: Dict[str, str]) -> str:
    """Update existing frontmatter values in place. Does not add new keys."""
    if not content.startswith('---'):
        return content
    end = content.find('---', 3)
    if end == -1:
        return content
    frontmatter_text = content[3:end]
    lines = frontmatter_text.split('\n')
    changed = False
    for i, line in enumerate(lines):
        if line and not line.startswith(' ') and ':' in line:
            key = line.split(':', 1)[0].strip()
            if key in updates:
                new_line = f'{key}: {updates[key]}'
                if lines[i] != new_line:
                    lines[i] = new_line
                    changed = True
    if not changed:
        return content
    return '---' + '\n'.join(lines) + '---' + content[end + 3:]


def extract_body_from_template(content: str) -> str:
    """Extract body content after frontmatter, removing Templater placeholders."""
    if content.startswith('---'):
        end = content.find('---', 3)
        if end != -1:
            content = content[end + 3:].strip()
    content = re.sub(r'<%.*?%>', '', content)
    return content


def extract_section(content: str, heading: str, level: int = 2) -> str:
    """Extract text under a markdown heading.

    Args:
        content: Full markdown file content
        heading: Heading text to search for (without # prefix)
        level: Heading level (1=# 2=## 3=###)

    Returns:
        Section text (without the heading line itself), or empty string
    """
    prefix = '#' * level
    pattern = rf'^{re.escape(prefix)}\s+{re.escape(heading)}\s*$'
    match = re.search(pattern, content, re.MULTILINE)
    if not match:
        return ''
    start = match.end()
    # Find next heading of same or higher level
    next_heading = re.search(r'^#{1,' + str(level) + r'}\s', content[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def extract_section_any_level(content: str, heading: str) -> str:
    """Like extract_section, but accepts the heading at level 2 or level 1.

    The vault template uses ``## Heading`` while the plugin's default template
    uses ``# Heading``; notes created from either must be readable.
    """
    return extract_section(content, heading, level=2) or extract_section(content, heading, level=1)


_RECENT_MEETINGS_RE = re.compile(r'^#{1,2} Recent Meetings\s*$', re.MULTILINE)
_AGENDA_RE = re.compile(r'^#{1,2} Agenda\s*$', re.MULTILINE)


def upsert_section(content: str, title: str, body: str) -> str:
    """Insert or replace a ``## <title>`` section in a meeting note.

    - If the section exists it is replaced in place (from its heading, including
      a preceding ``---`` separator, to the next ``---`` or level 1-2 heading).
    - Otherwise it is inserted before ``Recent Meetings``, else after the
      ``Agenda`` section, else appended. Both ``#`` and ``##`` headings are
      recognised for those anchors, so vault and plugin-default templates work.

    Returns the new content. Idempotent for identical input.
    """
    heading = f'## {title}'
    section = f'{heading}\n\n{body}'
    idx = content.find(heading)
    if idx != -1:
        section_start = idx
        prefix = content[:section_start]
        for sep in ['\n\n---\n', '\n---\n']:
            if prefix.endswith(sep):
                section_start -= len(sep)
                break
        search_from = idx + len(heading)
        sep_idx = content.find('\n---', search_from)
        heading_match = re.search(r'\n#{1,2} ', content[search_from:])
        section_end = len(content)
        if sep_idx != -1 and (heading_match is None or sep_idx <= search_from + heading_match.start()):
            section_end = sep_idx
        elif heading_match:
            section_end = search_from + heading_match.start()
        tail = content[section_end:].lstrip('\n')
        return (content[:section_start].rstrip() + '\n\n' + section
                + ('\n\n' + tail if tail else ''))

    rec_match = _RECENT_MEETINGS_RE.search(content)
    if rec_match:
        rec_idx = rec_match.start()
        insert_before = rec_idx
        for sep in ['\n---\n', '\n\n---\n']:
            if content[:rec_idx].endswith(sep):
                insert_before = rec_idx - len(sep)
                break
        return (content[:insert_before].rstrip() + '\n\n' + section
                + '\n\n' + content[insert_before:].lstrip())

    agenda_match = _AGENDA_RE.search(content)
    if agenda_match:
        next_heading = re.search(r'\n#{1,2} ', content[agenda_match.end():])
        next_section = agenda_match.end() + next_heading.start() if next_heading else -1
        separator_idx = content.find('\n---', agenda_match.end())
        insert_idx = len(content)
        if next_section != -1 and (separator_idx == -1 or next_section < separator_idx):
            insert_idx = next_section
        elif separator_idx != -1:
            insert_idx = separator_idx
        return content[:insert_idx].rstrip() + '\n\n' + section + '\n\n' + content[insert_idx:].lstrip()

    return content.rstrip() + '\n\n' + section + '\n'


def extract_gemini_summary(content: str) -> str:
    """Extract the ### Summary section from under ## Notes by Gemini."""
    gemini_section = extract_section(content, 'Notes by Gemini', level=2)
    if not gemini_section:
        return ''
    summary_match = re.search(
        r'#{1,4}\s*Summary\s*\n+(.*?)(?=\n+#{1,4}\s|\Z)',
        gemini_section, re.DOTALL | re.IGNORECASE
    )
    return summary_match.group(1).strip() if summary_match else ''


def extract_parking_lot(content: str) -> List[str]:
    """Extract bullet items from a # Parking Lot section.

    Handles variations: '# Parking Lot', '# Parking Lot 🚗', '# Parking Lot  🚗'.
    Returns a flat list of item strings (without leading bullet characters).
    Empty list if section is missing or has no bullets.
    """
    # Use a flexible regex that matches '# Parking Lot' with optional trailing emoji/whitespace
    match = re.search(r'^#\s+Parking Lot\b.*$', content, re.MULTILINE)
    if not match:
        return []
    start = match.end()
    # Find next heading of same or higher level (h1 = #)
    next_h = re.search(r'^#\s', content[start:], re.MULTILINE)
    end = start + next_h.start() if next_h else len(content)
    section = content[start:end]
    items = []
    for line in section.split('\n'):
        m = re.match(r'^(?:[-*]|\d+\.)\s+(.+)', line)
        if m:
            items.append(m.group(1).strip())
    return items


def load_template(vault_root: Path, template_name: str, skill_base_dir: Optional[Path] = None) -> str:
    """Load template body from vault or plugin default.

    Args:
        vault_root: Obsidian vault root path
        template_name: Template filename without extension
        skill_base_dir: Path to the skill directory (for plugin defaults fallback)
    """
    templates_config = vault_root / '.obsidian' / 'templates.json'
    if templates_config.exists():
        try:
            config = json.loads(templates_config.read_text())
            templates_folder = config.get('folder', 'TEMPLATES')
            vault_template = vault_root / templates_folder / f'{template_name}.md'
            if vault_template.exists():
                return extract_body_from_template(vault_template.read_text())
        except (json.JSONDecodeError, Exception):
            pass

    if skill_base_dir:
        plugin_default = skill_base_dir.parent / 'obsidian-vault-setup' / 'defaults' / 'templates' / f'{template_name}.md'
        if plugin_default.exists():
            return extract_body_from_template(plugin_default.read_text())

    return ''


if __name__ == '__main__':
    print('vault_utils.py -- shared utility library for daily-planner scripts.')
    print('Not intended for direct invocation. Import it from other scripts.')
    print()
    print('Exported functions:')
    for fn in [
        'sanitize_title(title)',
        'html_to_markdown(html_content)',
        'extract_doc_id_from_url(url)',
        'parse_frontmatter(content)',
        'update_frontmatter_with_missing_properties(content, new_props)',
        'update_frontmatter_values(content, updates)',
        'extract_body_from_template(content)',
        'extract_section(content, heading, level=2)',
        'extract_gemini_summary(content)',
        'extract_parking_lot(content)',
        'load_template(vault_root, template_name, skill_base_dir=None)',
    ]:
        print(f'  {fn}')
