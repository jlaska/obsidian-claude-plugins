#!/usr/bin/env python3
"""
List meeting notes for a day that link a Notion page, so the AI agent can fetch them.

Python scripts cannot call MCP tools, so sync_to_vault.py only records the
``notion:`` URL in each meeting file's frontmatter. This script reports which
meetings need a Notion fetch (via the ``notion-fetch`` MCP tool); the agent then
writes the result with ``write_notion_section.py``.

Output: JSON array, one element per meeting that has a ``notion:`` URL:
    stem, path, start, notion_url, page_id, is_database, has_section
Database-view links (``?v=``) are reported with ``is_database: true``; they are
linked only and must not be fetched.

Usage:
    python3 list_notion_meetings.py --vault-root /path/to/vault [--date YYYY-MM-DD]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from vault_utils import extract_notion_links, parse_frontmatter


def list_notion_meetings(vault_root: Path, date: datetime) -> List[Dict]:
    folder = vault_root / 'MEETINGS' / date.strftime('%Y') / date.strftime('%m-%B')
    prefix = date.strftime('%Y-%m-%d') + ' - '
    results: List[Dict] = []
    if not folder.is_dir():
        return results
    for meeting_file in sorted(folder.glob(f'{prefix}*.md')):
        content = meeting_file.read_text()
        url = parse_frontmatter(content).get('notion', '')
        if not url:
            continue
        links = extract_notion_links(url)
        if not links:
            continue
        results.append({
            'stem': meeting_file.stem,
            'path': str(meeting_file),
            'start': parse_frontmatter(content).get('start', ''),
            'notion_url': links[0]['url'],
            'page_id': links[0]['page_id'],
            'is_database': links[0]['is_database'],
            'has_section': '## Notes from Notion' in content,
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description='List meetings that link a Notion page')
    parser.add_argument('--vault-root', required=True)
    parser.add_argument('--date', help='YYYY-MM-DD (default: today)')
    args = parser.parse_args()
    date = datetime.strptime(args.date, '%Y-%m-%d') if args.date else datetime.now()
    print(json.dumps(list_notion_meetings(Path(args.vault_root), date), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
