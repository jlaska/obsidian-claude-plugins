#!/usr/bin/env python3
"""
Shared utilities for Obsidian vault operations.

Functions used by multiple daily-planner scripts (sync_to_vault.py,
gather_meeting_context.py, etc.).
"""

import json
import re
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
        m = re.match(r'^[-*]\s+(.+)', line)
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
