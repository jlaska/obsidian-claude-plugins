#!/usr/bin/env python3
"""
Write (insert or replace) the ``## Notes from Notion`` section of a meeting note.

The AI agent fetches the page with the ``notion-fetch`` MCP tool, extracts the
relevant section, cleans it, saves it to a file, and calls this script. Keeping
the write deterministic gives the same idempotency as the Gemini notes: the
section is replaced in place on every run, and everything else in the note is
left untouched.

Usage:
    python3 write_notion_section.py --meeting-file FILE --content-file CONTENT.md
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vault_utils import upsert_section

SECTION_TITLE = 'Notes from Notion'


def main() -> int:
    parser = argparse.ArgumentParser(description='Write Notes from Notion section')
    parser.add_argument('--meeting-file', required=True)
    parser.add_argument('--content-file', required=True)
    args = parser.parse_args()

    meeting_file = Path(args.meeting_file)
    if not meeting_file.exists():
        print(f'✗ Meeting file not found: {meeting_file}', file=sys.stderr)
        return 1
    body = Path(args.content_file).read_text().strip()
    if not body:
        print('✗ Content file is empty; nothing written', file=sys.stderr)
        return 1

    content = meeting_file.read_text()
    existed = f'## {SECTION_TITLE}' in content
    new_content = upsert_section(content, SECTION_TITLE, body)
    if new_content != content:
        meeting_file.write_text(new_content)
    print(f'  ✓ {"Updated" if existed else "Added"} Notion notes: {meeting_file.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
