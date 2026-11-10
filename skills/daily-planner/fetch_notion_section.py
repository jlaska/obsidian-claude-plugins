#!/usr/bin/env python3
"""
Convert a fetched Notion page and write ``## Notes from Notion`` into a meeting note.

The AI agent calls the ``notion-fetch`` MCP tool and saves the raw result to a
file; this script does everything else deterministically (section selection,
cleaning, source line, replace-in-place write via ``vault_utils.upsert_section``).

Usage:
    python3 fetch_notion_section.py --meeting-file FILE --date YYYY-MM-DD \
        --raw-file RAW [--notion-url URL] [--vault-root PATH]

Prints exactly one JSON line on stdout:
    {"status": "written|no_section|needs_child|error", "reason": "...",
     "matched_dates": [...], "child_candidate": {title, url}|null, "warnings": [...]}

Exit code 0 for written / no_section / needs_child, 1 for error. Nothing is
written to the meeting file unless the status is ``written``.

For ``needs_child`` (index page of per-date child pages), fetch the
``child_candidate`` URL and re-run this script with that child's raw file; the
child's own URL is used for the Source line automatically.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from notion_convert import convert
from vault_utils import extract_notion_links, parse_frontmatter, upsert_section

SECTION_TITLE = 'Notes from Notion'


def _status(status: str, reason: str = '', result: Optional[dict] = None,
            extra_warnings: Optional[List[str]] = None) -> dict:
    result = result or {}
    return {
        'status': status,
        'reason': reason,
        'matched_dates': result.get('matched_dates', []),
        'child_candidate': result.get('child_candidate'),
        'warnings': list(result.get('warnings', [])) + list(extra_warnings or []),
    }


def _find_vault_root(meeting_file: Path) -> Optional[Path]:
    for parent in meeting_file.resolve().parents:
        if (parent / 'PEOPLE').is_dir() or parent.name == 'MEETINGS':
            return parent.parent if parent.name == 'MEETINGS' else parent
    return None


def make_person_resolver(vault_root: Optional[Path], warnings: List[str]
                         ) -> Optional[Callable[[str, str], Optional[str]]]:
    """Resolve a Notion user mention to ``[[Person]]`` only when a PEOPLE note exists.

    Reuses ``sync_to_vault.match_attendee_to_person`` (frontmatter email, exact
    name, email-derived name) but disables its ``gog`` lookup and ignores its
    always-link fallback: an unmatched person stays a plain name.
    """
    if vault_root is None or not (vault_root / 'PEOPLE').is_dir():
        return None
    try:
        import sync_to_vault  # imported lazily: heavy and optional here
    except Exception as exc:
        warnings.append(f'person matching unavailable: {exc}')
        return None

    real_run = sync_to_vault._run

    def offline_run(cmd, timeout=30):
        if cmd and cmd[0] == 'gog':
            return None
        return real_run(cmd, timeout)

    def resolve(email: str, name: str) -> Optional[str]:
        sync_to_vault._run = offline_run
        try:
            link = sync_to_vault.match_attendee_to_person(email, name, vault_root)
        finally:
            sync_to_vault._run = real_run
        stem = link.strip().strip('"').removeprefix('[[').removesuffix(']]')
        if stem and (vault_root / 'PEOPLE' / f'{stem}.md').exists():
            return f'[[{stem}]]'
        return None

    return resolve


def _source_url(cli_url: Optional[str], meeting_content: str, raw_url: str) -> str:
    """CLI override > fetched child page URL > the meeting's ``notion:`` URL."""
    if cli_url:
        return cli_url
    meeting_links = extract_notion_links(parse_frontmatter(meeting_content).get('notion', ''))
    meeting_url = meeting_links[0]['url'] if meeting_links else ''
    raw_links = extract_notion_links(raw_url) if raw_url else []
    if raw_links and (not meeting_links or raw_links[0]['page_id'] != meeting_links[0]['page_id']):
        return raw_links[0]['url']
    return meeting_url or (raw_links[0]['url'] if raw_links else '')


def run(meeting_file: Path, date: str, raw_text: str, notion_url: Optional[str] = None,
        vault_root: Optional[Path] = None) -> dict:
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return _status('error', f'invalid --date {date!r} (expected YYYY-MM-DD)')
    if not meeting_file.exists():
        return _status('error', f'meeting file not found: {meeting_file}')

    warnings: List[str] = []
    vault_root = vault_root or _find_vault_root(meeting_file)
    resolver = make_person_resolver(vault_root, warnings)
    result = convert(raw_text, date, resolve_person=resolver)
    result['warnings'] = warnings + result.get('warnings', [])

    status = result['status']
    if status == 'error':
        return _status('error', result['reason'], result)
    if status in ('no_section', 'needs_child'):
        return _status(status, result['reason'], result)

    content = meeting_file.read_text()
    url = _source_url(notion_url, content, result.get('url', ''))
    source = f'Source: [Notion]({url})\n\n' if url else ''
    new_content = upsert_section(content, SECTION_TITLE, source + result['markdown'].strip())
    if new_content != content:
        meeting_file.write_text(new_content)
    return _status('written', '', result)


def ntn_get(page_id: str, timeout: int = 60) -> str:
    """Fetch a page as markdown with ``ntn pages get``. Raises RuntimeError on failure."""
    try:
        proc = subprocess.run(['ntn', 'pages', 'get', page_id], capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError('ntn not installed')
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'ntn timed out fetching {page_id}')
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(f'ntn failed for {page_id}: {msg[0] if msg else proc.returncode}')
    return proc.stdout


def run_ntn(meeting_file: Path, date: str, notion_url: Optional[str] = None,
            vault_root: Optional[Path] = None,
            getter: Callable[[str], str] = ntn_get) -> dict:
    """Fetch the meeting's Notion page via ntn (and the child page for index pages)."""
    if not meeting_file.exists():
        return _status('error', f'meeting file not found: {meeting_file}')
    url = notion_url
    if not url:
        links = extract_notion_links(parse_frontmatter(meeting_file.read_text()).get('notion', '') or '')
        url = links[0]['url'] if links else ''
    links = extract_notion_links(url or '')
    if not links:
        return _status('error', 'no Notion URL (pass --notion-url or set notion: frontmatter)')
    if links[0]['is_database']:
        return _status('error', 'database view link; link a specific page')
    try:
        raw = getter(links[0]['page_id'])
    except RuntimeError as exc:
        return _status('error', str(exc))
    out = run(meeting_file, date, raw, url, vault_root)
    child = out.get('child_candidate')
    if out['status'] == 'needs_child' and child:
        child_links = extract_notion_links(child.get('url', ''))
        if not child_links:
            return out
        try:
            raw = getter(child_links[0]['page_id'])
        except RuntimeError as exc:
            return _status('error', str(exc))
        out = run(meeting_file, date, raw, child['url'], vault_root)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description='Write Notes from Notion section')
    parser.add_argument('--meeting-file', required=True)
    parser.add_argument('--date', required=True, help='YYYY-MM-DD')
    parser.add_argument('--ntn', action='store_true',
                        help='fetch the page with the ntn CLI instead of --raw-file')
    parser.add_argument('--raw-file',
                        help='file holding the raw notion-fetch result (JSON or its text)')
    parser.add_argument('--notion-url', help='URL for the Source line (default: meeting notion: '
                                             'frontmatter, or the fetched child page URL)')
    parser.add_argument('--vault-root', help='vault root for PEOPLE matching (default: inferred)')
    args = parser.parse_args()

    if args.ntn:
        try:
            out = run_ntn(Path(args.meeting_file), args.date, args.notion_url,
                          Path(args.vault_root) if args.vault_root else None)
        except Exception as exc:
            out = _status('error', f'{type(exc).__name__}: {exc}')
        print(json.dumps(out, ensure_ascii=False))
        if out['status'] == 'error':
            print(f"✗ {out['reason']}", file=sys.stderr)
            return 1
        return 0
    if not args.raw_file:
        parser.error('--raw-file is required unless --ntn is given')
    try:
        raw_text = Path(args.raw_file).read_text(encoding='utf-8')
    except OSError as exc:
        out = _status('error', f'cannot read --raw-file: {exc}')
    else:
        try:
            out = run(Path(args.meeting_file), args.date, raw_text, args.notion_url,
                      Path(args.vault_root) if args.vault_root else None)
        except Exception as exc:
            out = _status('error', f'{type(exc).__name__}: {exc}')
    print(json.dumps(out, ensure_ascii=False))
    if out['status'] == 'error':
        print(f"✗ {out['reason']}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
