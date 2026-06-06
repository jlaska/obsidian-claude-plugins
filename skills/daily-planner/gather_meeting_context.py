#!/usr/bin/env python3
"""
Gather meeting context from the Obsidian vault for AI meeting preparation.

Reads the daily note meetings table, classifies each meeting as upcoming or
past, finds previous meetings via obsidian search (with grep fallback), extracts
raw text sections from previous meeting files, and gathers Parking Lot items from
PEOPLE/ files.

Output: JSON with meeting array for AI synthesis.

Usage:
    python3 gather_meeting_context.py --vault-root /path/to/vault [--date YYYY-MM-DD]
    python3 gather_meeting_context.py --vault-root /path/to/vault --self-json self.json
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from vault_utils import extract_parking_lot, extract_section, parse_frontmatter


def _run(cmd: list, timeout: int = 15) -> Optional[str]:
    """Run a subprocess and return stdout, or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Daily note table parsing
# ---------------------------------------------------------------------------

def parse_meetings_table(daily_note_content: str) -> List[Dict]:
    """Parse the # Meetings table from a daily note.

    Returns a list of dicts with: stem, display_title, time, attendees (raw text).

    Table rows may contain escaped pipes (\\|) inside wikilinks, so we parse the
    entire row with a regex rather than splitting naively on '|'.
    """
    meetings = []
    in_table = False
    for line in daily_note_content.splitlines():
        if line.startswith('# 📅 Meetings') or line.startswith('# Meetings'):
            in_table = True
            continue
        if in_table:
            if line.startswith('#'):
                break  # Next section
            if not line.startswith('|'):
                continue
            # Skip header/separator rows
            if re.match(r'^\|[\s-]+\|', line):
                continue
            if '| Time' in line or '| time' in line:
                continue

            # Extract time (first pipe-delimited cell)
            time_match = re.match(r'^\|\s*([^|]+?)\s*\|', line)
            if not time_match:
                continue
            time_str = time_match.group(1).strip()
            if time_str.startswith('Time') or time_str.startswith('-'):
                continue

            # Extract first wikilink from the line (the meeting link)
            # Wikilinks in tables use \| for the pipe separator, e.g. [[stem\|title]]
            wikilink_match = re.search(r'\[\[([^\]\\]+)(?:\\[|]([^\]]*))?\]\]', line)
            if not wikilink_match:
                continue
            stem = wikilink_match.group(1).strip()
            display_title = (wikilink_match.group(2) or stem).strip()

            # Extract attendee wikilinks (all [[Name]] after the meeting link)
            # Find position after the first wikilink and search there
            after_meeting = line[wikilink_match.end():]
            attendees = re.findall(r'\[\[([^\]\\]+)(?:\\[|][^\]]*)?\]\]', after_meeting)

            meetings.append({
                'stem': stem,
                'display_title': display_title,
                'time': time_str,
                'attendees': [f'[[{a}]]' for a in attendees],
            })
    return meetings


def _has_meeting_preparation_section(daily_note_content: str) -> bool:
    """Return True if a # Meeting Preparation section already exists."""
    return bool(re.search(r'^# Meeting Preparation', daily_note_content, re.MULTILINE))


# ---------------------------------------------------------------------------
# Meeting file introspection
# ---------------------------------------------------------------------------

def classify_meeting_time(start_iso: str, now: datetime) -> str:
    """Return 'upcoming' or 'past' based on start time vs now."""
    try:
        start = datetime.fromisoformat(start_iso)
        # Make both offset-aware for comparison
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return 'upcoming' if start > now else 'past'
    except (ValueError, TypeError):
        return 'upcoming'


def get_meeting_type(attendees: List[str]) -> str:
    """Return 'one_on_one' if exactly 1 non-owner attendee, else 'group'."""
    # attendees is the list from the meeting file frontmatter (may include owner)
    return 'one_on_one' if len(attendees) == 1 else 'group'


def read_meeting_frontmatter(meeting_file: Path) -> Dict:
    """Read and return parsed frontmatter dict from a meeting file."""
    if not meeting_file.exists():
        return {}
    content = meeting_file.read_text()
    fm = parse_frontmatter(content)
    # Also extract multi-line attendees list
    attendees = _extract_attendees_list(content)
    if attendees:
        fm['_attendees_list'] = attendees
    return fm


def _extract_attendees_list(content: str) -> List[str]:
    """Extract the attendees YAML list from frontmatter."""
    if not content.startswith('---'):
        return []
    end = content.find('---', 3)
    if end == -1:
        return []
    fm_block = content[3:end]
    attendees = []
    in_attendees = False
    for line in fm_block.split('\n'):
        if re.match(r'^attendees:', line):
            in_attendees = True
            continue
        if in_attendees:
            if re.match(r'^  - ', line):
                val = line.strip().lstrip('- ').strip().strip('"')
                attendees.append(val)
            elif line.strip() and not line.startswith(' '):
                break
    return attendees


# ---------------------------------------------------------------------------
# Previous meeting discovery
# ---------------------------------------------------------------------------

def _obsidian_search(query: str, vault_root: Path, limit: int = 5) -> List[Path]:
    """Run obsidian search and return matching paths relative to vault_root."""
    out = _run(['obsidian', 'search', f'query={query}', 'path=MEETINGS/', f'limit={limit}'])
    if not out:
        return []
    paths = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            p = vault_root / line
            if p.exists():
                paths.append(p)
    return paths


def _grep_search_recurring(recurring_id: str, vault_root: Path) -> List[Path]:
    """Grep fallback: find meetings by recurringEventId."""
    # Strip _R<timestamp> suffix if present
    base_id = re.split(r'_R\d+', recurring_id)[0]
    meetings_dir = vault_root / 'MEETINGS'
    try:
        result = subprocess.run(
            ['grep', '-r', '-l', base_id, str(meetings_dir)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return [Path(p) for p in result.stdout.strip().splitlines() if p.strip()]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return []


def _find_search_by_names(names: List[str], vault_root: Path) -> List[Path]:
    """Find fallback: find meeting files containing all given name fragments."""
    meetings_dir = vault_root / 'MEETINGS'
    try:
        all_files = list(meetings_dir.rglob('*.md'))
        results = []
        for f in all_files:
            stem_lower = f.stem.lower()
            if all(n.lower() in stem_lower for n in names):
                results.append(f)
        return results
    except OSError:
        return []


def _strip_recurring_suffix(recurring_id: str) -> str:
    """Strip _R<timestamp> suffix from a recurringEventId."""
    return re.split(r'_R\d+', recurring_id)[0]


def _extract_first_name(wikilink: str) -> str:
    """Extract first name from a wikilink like [[Alice Tester]]."""
    name = wikilink.strip('[]').split('|')[0].strip()
    return name.split()[0] if name else name


def find_previous_meetings(
    stem: str,
    recurring_id: Optional[str],
    attendees: List[str],
    meeting_type: str,
    owner_first_name: str,
    vault_root: Path,
    limit: int = 3,
) -> List[Path]:
    """Find previous meetings for a given meeting.

    Uses two-tier strategy:
    - Tier 1 (recurring): obsidian search by recurringEventId, grep fallback
    - Tier 2 (non-recurring): obsidian search by filename, find fallback

    Excludes today's file (stem). Returns up to `limit` files, sorted by name
    descending (most recent first), excluding today.
    """
    candidates: List[Path] = []

    if recurring_id:
        base_id = _strip_recurring_suffix(recurring_id)
        candidates = _obsidian_search(f'[recurringEventId:{base_id}]', vault_root, limit=limit + 2)
        if not candidates:
            candidates = _grep_search_recurring(recurring_id, vault_root)
    else:
        if meeting_type == 'one_on_one' and attendees:
            attendee_first = _extract_first_name(attendees[0])
            query = f'file:"{attendee_first}" file:"{owner_first_name}"'
            candidates = _obsidian_search(query, vault_root, limit=limit + 2)
            if not candidates:
                candidates = _find_search_by_names([attendee_first, owner_first_name], vault_root)
        else:
            # Group meeting: search by meeting title words
            title_words = re.sub(r'\d{4}-\d{2}-\d{2}\s*-?\s*', '', stem).strip()
            if title_words:
                candidates = _obsidian_search(f'file:"{title_words}"', vault_root, limit=limit + 2)
                if not candidates:
                    candidates = _find_search_by_names(title_words.split(), vault_root)

    # Exclude today's file and sort descending by name
    today_stem = stem
    candidates = [c for c in candidates if c.stem != today_stem]
    candidates = sorted(set(candidates), key=lambda p: p.stem, reverse=True)
    return candidates[:limit]


# ---------------------------------------------------------------------------
# Context extraction from previous meetings
# ---------------------------------------------------------------------------

def extract_meeting_context(meeting_file: Path) -> Dict:
    """Extract raw text sections from a previous meeting file.

    Returns a dict with: stem, path, gemini_summary, actions_text, agenda_text.
    These are raw strings for AI to synthesize — not pre-summarized.
    """
    if not meeting_file.exists():
        return {}
    content = meeting_file.read_text()
    return {
        'stem': meeting_file.stem,
        'path': str(meeting_file),
        'gemini_summary': extract_section(content, 'Summary', level=3)
                          or extract_section(content, 'Notes by Gemini', level=2),
        'actions_text': extract_section(content, 'Actions', level=2),
        'agenda_text': extract_section(content, 'Agenda', level=2),
    }


# ---------------------------------------------------------------------------
# Main context gathering
# ---------------------------------------------------------------------------

def gather_context(
    vault_root: Path,
    date: Optional[datetime] = None,
    owner_first_name: str = 'James',
) -> Dict:
    """Gather all meeting context for AI preparation.

    Returns a JSON-serializable dict.
    """
    if date is None:
        date = datetime.now()

    now = datetime.now().astimezone()

    # Compute daily note path
    year = date.strftime('%Y')
    month_num = date.strftime('%m')
    month_name = date.strftime('%B')
    date_part = date.strftime('%Y-%m-%d')
    day_name = date.strftime('%A')
    daily_note_path = vault_root / 'DAILY_NOTES' / year / f'{month_num}-{month_name}' / f'{date_part} {day_name}.md'

    if not daily_note_path.exists():
        return {
            'date': date_part,
            'is_first_run': True,
            'meetings': [],
            'error': f'Daily note not found: {daily_note_path}',
        }

    daily_note_content = daily_note_path.read_text()
    is_first_run = not _has_meeting_preparation_section(daily_note_content)
    table_rows = parse_meetings_table(daily_note_content)

    meetings_dir = vault_root / 'MEETINGS' / year / f'{month_num}-{month_name}'
    meetings = []

    for row in table_rows:
        stem = row['stem']
        meeting_file = meetings_dir / f'{stem}.md'

        fm = read_meeting_frontmatter(meeting_file)
        start_iso = fm.get('start', '')
        status = classify_meeting_time(start_iso, now)

        attendees = fm.get('_attendees_list', row['attendees'])
        meeting_type = get_meeting_type(attendees)
        recurring_id = fm.get('recurringEventId', '')

        previous_paths = find_previous_meetings(
            stem=stem,
            recurring_id=recurring_id or None,
            attendees=attendees,
            meeting_type=meeting_type,
            owner_first_name=owner_first_name,
            vault_root=vault_root,
        )
        previous_meetings = [extract_meeting_context(p) for p in previous_paths]
        previous_meetings = [m for m in previous_meetings if m]

        # Parking lot: only for 1:1 meetings
        parking_lot: List[str] = []
        if meeting_type == 'one_on_one' and attendees:
            person_name = attendees[0].strip('[]').split('|')[0].strip()
            person_file = vault_root / 'PEOPLE' / f'{person_name}.md'
            if person_file.exists():
                parking_lot = extract_parking_lot(person_file.read_text())

        meetings.append({
            'stem': stem,
            'display_title': row['display_title'],
            'time': row['time'],
            'start_iso': start_iso,
            'status': status,
            'type': meeting_type,
            'attendees': attendees,
            'recurring_event_id': recurring_id or None,
            'previous_meetings': previous_meetings,
            'parking_lot': parking_lot,
        })

    return {
        'date': date_part,
        'is_first_run': is_first_run,
        'meetings': meetings,
    }


def main():
    parser = argparse.ArgumentParser(description='Gather meeting context for AI preparation')
    parser.add_argument('--vault-root', required=True, help='Obsidian vault root path')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: today)')
    parser.add_argument('--self-json', help='Path to discover_self.py output JSON')
    parser.add_argument('--owner-first-name', default='', help='Vault owner first name')
    args = parser.parse_args()

    date = datetime.now()
    if args.date:
        try:
            date = datetime.strptime(args.date, '%Y-%m-%d')
        except ValueError:
            print(f'Error: Invalid date: {args.date}', file=sys.stderr)
            sys.exit(1)

    owner_first_name = args.owner_first_name
    if not owner_first_name and args.self_json:
        try:
            with open(args.self_json) as f:
                self_data = json.load(f)
            owner_first_name = self_data.get('first_name', '')
        except (OSError, json.JSONDecodeError):
            pass

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        print(f'Error: vault root does not exist: {vault_root}', file=sys.stderr)
        sys.exit(1)

    context = gather_context(vault_root=vault_root, date=date, owner_first_name=owner_first_name)
    print(json.dumps(context, indent=2))


if __name__ == '__main__':
    main()
