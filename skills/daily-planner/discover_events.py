#!/usr/bin/env python3
"""
Fetch and filter Google Calendar events from all authenticated gog accounts.

Replaces the calendar-fetching portion of process_calendar.py. Discovers all
gog accounts, fetches events from each, merges and deduplicates by iCalUID,
and applies the standard filtering rules (skip declined, cancelled, self-only,
working-location, broadcast events).

Output: JSON array of filtered calendar events to stdout.

Usage:
    python3 discover_events.py [--date YYYY-MM-DD] [--cache-dir DIR] [--self-json PATH]
    python3 discover_events.py --self-json <(python3 discover_self.py)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set


def _run(cmd: list, timeout: int = 30) -> Optional[subprocess.CompletedProcess]:
    """Run a subprocess and return the result, or None on failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f'  ⚠️  Command failed: {" ".join(cmd)}: {e}', file=sys.stderr)
        return None


def discover_accounts() -> List[Dict]:
    """Discover all gog-authenticated accounts that have calendar access."""
    result = _run(['gog', 'auth', 'list', '--plain'])
    if not result or result.returncode != 0:
        return []
    accounts = []
    for line in result.stdout.strip().splitlines():
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        email = parts[0]
        client = parts[1]
        scopes = parts[2] if len(parts) > 2 else ''
        if 'calendar' in scopes:
            accounts.append({'email': email, 'client': client})
    return accounts


def fetch_account_events(
    account: Dict,
    date: datetime,
    cache_dir: Optional[Path] = None,
) -> Optional[List[Dict]]:
    """Fetch calendar events for one gog account for the given date."""
    email = account['email']
    client = account.get('client', '')

    date_str = date.strftime('%Y-%m-%d')
    # Use --today shortcut when fetching for today, otherwise specify range
    today_str = datetime.now().strftime('%Y-%m-%d')
    if date_str == today_str:
        date_flags = ['--today']
    else:
        # Fetch events for the specified day using gog's --from/--to flags
        from datetime import timedelta
        next_day = (date + timedelta(days=1)).strftime('%Y-%m-%d')
        date_flags = [f'--from={date_str}', f'--to={next_day}']

    cmd = ['gog', 'calendar', 'events', '--account', email] + date_flags + [
        '--json', '--all-pages', '--all'
    ]
    if client and client not in ('', 'default'):
        cmd.extend(['--client', client])

    result = _run(cmd, timeout=30)
    if not result or result.returncode != 0:
        print(f'  ⚠️  Failed to fetch events for {email}', file=sys.stderr)
        if result:
            print(f'      {result.stderr.strip()}', file=sys.stderr)
            print(f'      Run: gog auth add {email}', file=sys.stderr)
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f'  ⚠️  Invalid JSON from {email}', file=sys.stderr)
        return None

    events = data.get('events', [])

    if cache_dir:
        import re
        sanitized = re.sub(r'[@.]', '_', email)
        cache_path = cache_dir / f'calendar_events_{sanitized}.json'
        cache_path.write_text(result.stdout)

    return events


def merge_events(event_lists: List[List[Dict]]) -> List[Dict]:
    """Merge event lists from multiple accounts, deduplicating by iCalUID."""
    seen: Dict[str, bool] = {}
    merged: List[Dict] = []
    for events in event_lists:
        for event in events:
            key = (
                event.get('iCalUID')
                or f"{event.get('summary', '')}|{event.get('start', {}).get('dateTime', '')}"
            )
            if key not in seen:
                seen[key] = True
                merged.append(event)
    return merged


def should_skip_event(event: Dict, user_emails: Set[str]) -> bool:
    """Return True if this event should be excluded (not a real meeting)."""
    # Working location events
    if event.get('eventType') == 'workingLocation':
        return True

    # Cancelled events
    if event.get('status') == 'cancelled':
        return True

    attendees = event.get('attendees', [])

    # User has not accepted
    for attendee in attendees:
        if attendee.get('email', '').lower() in user_emails:
            if attendee.get('responseStatus') not in ('accepted',):
                return True

    # No attendees at all, or only the user themselves
    if not attendees:
        return True
    non_self = [a for a in attendees if a.get('email', '').lower() not in user_emails]
    if not non_self:
        return True

    # Broadcast events (user can't see or invite others)
    if (event.get('guestsCanSeeOtherGuests') is False
            and event.get('guestsCanInviteOthers') is False):
        return True

    return False


def filter_events(events: List[Dict], user_emails: Set[str]) -> List[Dict]:
    """Apply all filtering rules to a list of events."""
    return [e for e in events if not should_skip_event(e, user_emails)]


def load_user_emails_from_self_json(path: str) -> Set[str]:
    """Load user emails from a discover_self.py JSON output file."""
    try:
        with open(path) as f:
            data = json.load(f)
        return {e.lower() for e in data.get('emails', [])}
    except (OSError, json.JSONDecodeError) as e:
        print(f'  ⚠️  Could not load self JSON from {path}: {e}', file=sys.stderr)
        return set()


def discover_events(
    date: Optional[datetime] = None,
    user_emails: Optional[Set[str]] = None,
    cache_dir: Optional[Path] = None,
) -> List[Dict]:
    """Discover, fetch, merge, and filter calendar events.

    Args:
        date: Target date (default: today)
        user_emails: Set of the user's own email addresses for self-filtering
        cache_dir: Optional directory to cache raw per-account JSON files

    Returns:
        List of filtered calendar event dicts
    """
    if date is None:
        date = datetime.now()
    if user_emails is None:
        user_emails = set()

    accounts = discover_accounts()
    if not accounts:
        print('  ⚠️  No gog accounts with calendar access found', file=sys.stderr)
        return []

    all_event_lists: List[List[Dict]] = []
    for account in accounts:
        events = fetch_account_events(account, date, cache_dir=cache_dir)
        if events is not None:
            all_event_lists.append(events)

    merged = merge_events(all_event_lists)
    filtered = filter_events(merged, user_emails)
    return filtered


def main():
    parser = argparse.ArgumentParser(description='Fetch and filter Google Calendar events')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: today)')
    parser.add_argument('--cache-dir', help='Directory to cache raw event JSON files')
    parser.add_argument(
        '--self-json',
        help='Path to discover_self.py output JSON (for user email filtering)',
    )
    parser.add_argument(
        '--user-emails',
        help='Comma-separated list of user email addresses',
    )
    args = parser.parse_args()

    date = datetime.now()
    if args.date:
        try:
            date = datetime.strptime(args.date, '%Y-%m-%d')
        except ValueError:
            print(f'Error: Invalid date format: {args.date}', file=sys.stderr)
            sys.exit(1)

    user_emails: Set[str] = set()
    if args.self_json:
        user_emails = load_user_emails_from_self_json(args.self_json)
    elif args.user_emails:
        user_emails = {e.strip().lower() for e in args.user_emails.split(',')}

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    events = discover_events(date=date, user_emails=user_emails, cache_dir=cache_dir)
    print(json.dumps({'events': events}, indent=2))


if __name__ == '__main__':
    main()
