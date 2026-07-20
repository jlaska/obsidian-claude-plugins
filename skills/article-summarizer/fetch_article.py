#!/usr/bin/env python3
"""
Fetch web article metadata, create Obsidian reference notes.

Modes:
  Fetch mode (default):
    python3 fetch_article.py <vault_root> <url> --title "..." --author "..." \
        --published "YYYY-MM-DD" --source "example.com"
    - Creates skeleton note in REFERENCES/ using today's date
    - Outputs JSON summary to stdout

  Save-summary mode:
    python3 fetch_article.py <vault_root> <url> --save-summary <json_path>
    - Reads summary JSON (tldr, summary, takeaways, tags, vault_connections, recommendations)
    - Injects content into existing note
    - Updates frontmatter tags
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

_TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'msclkid', 'ref', 'mc_cid', 'mc_eid',
}


def normalize_url(url: str) -> str:
    """Strip tracking params, normalize scheme to https."""
    parsed = urlparse(url)
    scheme = 'https'
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    cleaned = {k: v for k, v in query_params.items() if k.lower() not in _TRACKING_PARAMS}
    new_query = urlencode({k: v[0] for k, v in sorted(cleaned.items())}, doseq=False)
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, new_query, ''))


def url_hash(url: str) -> str:
    """Return 8-char hex hash of normalized URL for use in cache filenames."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:8]


def url_source(url: str) -> str:
    """Extract hostname without www prefix as a readable source label."""
    host = urlparse(url).netloc
    return host.removeprefix('www.')


# ---------------------------------------------------------------------------
# File path utilities
# ---------------------------------------------------------------------------

def sanitize_title(title: str) -> str:
    """Sanitize article title for use as filesystem name."""
    title = title.replace('/', ' - ')
    title = title.replace(':', ' - ')
    title = title.replace('|', ' - ')
    title = re.sub(r'[<>"\\?*]', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title.strip()


def get_date_subfolder(dt: datetime) -> str:
    """Return YYYY/MM-Month folder string."""
    return f"{dt.strftime('%Y')}/{dt.strftime('%m')}-{dt.strftime('%B')}"


def get_note_path(vault_root: Path, safe_title: str, dt: datetime) -> Path:
    """Return full path for REFERENCES note using today's date (consumption date)."""
    date_str = dt.strftime('%Y-%m-%d')
    subfolder = get_date_subfolder(dt)
    return vault_root / 'REFERENCES' / subfolder / f"{date_str} - {safe_title}.md"


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

TEMPLATE_NAME = "Article Summarization Template.md"


def load_article_template(vault_root: Path) -> Optional[str]:
    """Load Article Summarization Template from vault or fallback to plugin default.

    Checks vault's configured templates folder (from .obsidian/templates.json),
    then falls back to the defaults/templates/ directory alongside this script.
    Returns full file content (including frontmatter), or None if not found.
    """
    # 1. Try vault's template config
    templates_config = vault_root / ".obsidian" / "templates.json"
    if templates_config.exists():
        try:
            config = json.loads(templates_config.read_text())
            templates_folder = config.get('folder', 'TEMPLATES')
            vault_template = vault_root / templates_folder / TEMPLATE_NAME
            if vault_template.exists():
                return vault_template.read_text()
        except Exception:
            pass

    # 2. Fallback to plugin default
    plugin_default = Path(__file__).parent / "defaults" / "templates" / TEMPLATE_NAME
    if plugin_default.exists():
        return plugin_default.read_text()

    return None


def render_template(template: str, variables: dict) -> str:
    """Replace {{key}} placeholders in template with values from variables dict."""
    for key, value in variables.items():
        template = template.replace(f'{{{{{key}}}}}', str(value))
    return template


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_skeleton_note(path: Path, metadata: dict, vault_root: Optional[Path] = None) -> None:
    """Write skeleton reference note to REFERENCES/ with empty body sections."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created_dt = datetime.now().strftime('%Y-%m-%d %H:%M')

    title = metadata.get('title', '')
    author = metadata.get('author', '')
    url = metadata['url']
    published = metadata.get('published', '')
    source = metadata.get('source', url_source(url))

    # Try template-driven approach first
    if vault_root is not None:
        template = load_article_template(vault_root)
        if template is not None:
            variables = {
                'title': title,
                'author': author,
                'url': url,
                'published': published,
                'source': source,
                'created': created_dt,
            }
            path.write_text(render_template(template, variables))
            return

    # Hardcoded fallback
    lines = [
        '---',
        f'title: "{title}"',
        f'author: "{author}"',
        f'url: {url}',
        f'published: {published}',
        f'source: "{source}"',
        'tags:',
        '  - References',
        '  - Articles',
        f'created: {created_dt}',
        '---',
        '',
        '## TLDR',
        '',
        '',
        '## Summary',
        '',
        '',
        '## Key Takeaways',
        '',
        '',
        '## Vault Connections',
        '',
        '',
        '## Recommendations',
        '',
        '',
    ]
    path.write_text('\n'.join(lines))


def save_summary(note_path: Path, summary_json: dict) -> None:
    """Inject LLM-generated content into existing skeleton note."""
    if not note_path.exists():
        raise FileNotFoundError(f"Note not found: {note_path}")

    content = note_path.read_text()

    new_tags = summary_json.get('tags', [])
    if new_tags:
        content = _merge_frontmatter_tags(content, new_tags)

    tldr = summary_json.get('tldr', '').strip()
    if tldr:
        content = _replace_section(content, '## TLDR', tldr)

    summary_text = summary_json.get('summary', '').strip()
    if summary_text:
        content = _replace_section(content, '## Summary', summary_text)

    takeaways = summary_json.get('takeaways', [])
    if takeaways:
        bullets = '\n'.join(f'- {t}' for t in takeaways)
        content = _replace_section(content, '## Key Takeaways', bullets)

    vault_connections = summary_json.get('vault_connections', '').strip()
    if vault_connections:
        content = _replace_section(content, '## Vault Connections', vault_connections)

    recommendations = summary_json.get('recommendations', '').strip()
    if recommendations:
        content = _replace_section(content, '## Recommendations', recommendations)

    note_path.write_text(content)


def _merge_frontmatter_tags(content: str, new_tags: list) -> str:
    """Add new tags to frontmatter tags list without duplicating existing ones."""
    if not content.startswith('---'):
        return content

    end = content.find('---', 3)
    if end == -1:
        return content

    frontmatter = content[3:end]
    after = content[end:]

    tags_match = re.search(r'^tags:\n((?:  - .+\n)*)', frontmatter, re.MULTILINE)
    if not tags_match:
        return content

    existing_block = tags_match.group(0)
    existing_tags = re.findall(r'^  - (.+)$', existing_block, re.MULTILINE)

    merged = list(existing_tags)
    for tag in new_tags:
        if tag not in merged:
            merged.append(tag)

    new_block = 'tags:\n' + ''.join(f'  - {t}\n' for t in merged)
    new_frontmatter = frontmatter[:tags_match.start()] + new_block + frontmatter[tags_match.end():]
    return '---' + new_frontmatter + after


def _replace_section(content: str, heading: str, new_body: str) -> str:
    """Replace the body of a markdown section identified by `heading`.

    Preserves content in subsequent sections. If heading not found, appends.
    """
    heading_pattern = re.escape(heading)
    match = re.search(rf'^{heading_pattern}\s*$', content, re.MULTILINE)
    if not match:
        return content.rstrip() + f'\n\n{heading}\n\n{new_body}\n'

    start = match.end()
    m = re.match(r'^(#+)', heading)
    level = len(m.group(1)) if m else 2
    next_heading = re.search(r'^#{1,' + str(level) + r'} ', content[start:], re.MULTILINE)

    if next_heading:
        section_end = start + next_heading.start()
        return content[:start] + f'\n{new_body}\n\n' + content[section_end:]
    else:
        return content[:start] + f'\n{new_body}\n'


# ---------------------------------------------------------------------------
# Existing note detection
# ---------------------------------------------------------------------------

def find_existing_note(vault_root: Path, url: str) -> Optional[Path]:
    """Search REFERENCES/ for a note containing the given URL in its frontmatter."""
    references_dir = vault_root / 'REFERENCES'
    if not references_dir.exists():
        return None

    norm = normalize_url(url)
    for md_file in references_dir.rglob('*.md'):
        try:
            content = md_file.read_text()
            # Check the first 30 lines (frontmatter only)
            frontmatter_text = '\n'.join(content.splitlines()[:30])
            if url in frontmatter_text or norm in frontmatter_text:
                return md_file
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# Main modes
# ---------------------------------------------------------------------------

def fetch_mode(vault_root: Path, url: str, title: str, author: str,
               published: str, source: str) -> dict:
    """Create skeleton reference note and return JSON summary."""
    norm_url = normalize_url(url)
    today = datetime.now()

    # Use provided title or fall back to URL hostname
    effective_title = title.strip() if title.strip() else url_source(url)
    safe_title = sanitize_title(effective_title)
    effective_source = source.strip() if source.strip() else url_source(url)

    note_path = get_note_path(vault_root, safe_title, today)

    # Check for existing note by URL
    existing = find_existing_note(vault_root, url)
    if existing:
        return {
            'title': effective_title,
            'author': author,
            'published': published,
            'source': effective_source,
            'url': norm_url,
            'note_path': str(existing),
            'already_exists': True,
        }

    metadata = {
        'title': effective_title,
        'author': author.strip(),
        'url': norm_url,
        'published': published.strip(),
        'source': effective_source,
    }

    print(f"Creating reference note: {note_path}", file=sys.stderr)
    write_skeleton_note(note_path, metadata, vault_root=vault_root)
    print("Done.", file=sys.stderr)

    return {
        'title': effective_title,
        'author': author,
        'published': published,
        'source': effective_source,
        'url': norm_url,
        'note_path': str(note_path),
        'already_exists': False,
    }


def save_summary_mode(vault_root: Path, url: str, summary_json_path: str) -> dict:
    """Load summary JSON and inject into existing note."""
    with open(summary_json_path, 'r') as f:
        summary_data = json.load(f)

    note_path = find_existing_note(vault_root, url)
    if note_path is None:
        raise FileNotFoundError(
            f"No existing note found for URL: {url}\n"
            "Run without --save-summary first to create the note."
        )

    print(f"Saving summary to: {note_path}", file=sys.stderr)
    save_summary(note_path, summary_data)
    print("Done.", file=sys.stderr)

    return {
        'url': url,
        'note_path': str(note_path),
        'saved': True,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Create Obsidian reference note for a web article'
    )
    parser.add_argument('vault_root', help='Path to Obsidian vault root')
    parser.add_argument('url', help='Article URL')
    parser.add_argument('--title', default='', help='Article title (extracted by WebFetch)')
    parser.add_argument('--author', default='', help='Article author(s)')
    parser.add_argument('--published', default='', help='Publication date (YYYY-MM-DD)')
    parser.add_argument('--source', default='', help='Site/publication name')
    parser.add_argument(
        '--save-summary',
        metavar='JSON_PATH',
        help='Path to summary JSON; inject into existing note instead of creating skeleton'
    )

    args = parser.parse_args()

    vault_root = Path(args.vault_root)
    if not vault_root.is_dir():
        print(json.dumps({'error': f"Vault root not found: {vault_root}"}))
        sys.exit(1)

    try:
        if args.save_summary:
            result = save_summary_mode(vault_root, args.url, args.save_summary)
        else:
            result = fetch_mode(
                vault_root, args.url,
                title=args.title,
                author=args.author,
                published=args.published,
                source=args.source,
            )

        print(json.dumps(result, indent=2))

    except ValueError as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)
    except FileNotFoundError as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({'error': f"Unexpected error: {e}"}))
        sys.exit(1)


if __name__ == '__main__':
    main()
