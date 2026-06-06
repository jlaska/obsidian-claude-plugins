#!/usr/bin/env python3
"""
Discover user identity across available sources.

Aggregates identity from OS environment, gog CLI (Google accounts), and
environment variables. Used to resolve "who is the vault owner" for
self-attendee filtering and meeting search queries.

Output: JSON with username, emails, display_name, first_name.

Usage:
    python3 discover_self.py
"""

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Set


def _run(cmd: list, timeout: int = 10) -> Optional[str]:
    """Run a subprocess and return stdout, or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _discover_gog_accounts() -> List[Dict]:
    """List all gog-authenticated accounts."""
    out = _run(['gog', 'auth', 'list', '--plain'])
    if not out:
        return []
    accounts = []
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 2:
            accounts.append({'email': parts[0], 'client': parts[1]})
    return accounts


def _whoami_for_account(account: Dict) -> Optional[Dict]:
    """Run gog whoami for a specific account and return the person dict."""
    cmd = ['gog', 'whoami', '--json', '--account', account['email']]
    client = account.get('client', '')
    if client and client not in ('', 'default'):
        cmd.extend(['--client', client])
    out = _run(cmd, timeout=15)
    if not out:
        return None
    try:
        return json.loads(out).get('person', {})
    except json.JSONDecodeError:
        return None


def _extract_name(person: Dict) -> Optional[str]:
    """Extract display name from a gog whoami person dict."""
    names = person.get('names', [])
    for entry in names:
        if entry.get('metadata', {}).get('primary'):
            return entry.get('displayName')
    if names:
        return names[0].get('displayName')
    return None


def _extract_emails(person: Dict) -> Set[str]:
    """Extract all email addresses from a gog whoami person dict."""
    emails: Set[str] = set()
    for entry in person.get('emailAddresses', []):
        val = entry.get('value', '').strip()
        if val and '@' in val:
            emails.add(val.lower())
    return emails


def discover_self() -> Dict:
    """Discover the current user's identity.

    Returns a dict suitable for JSON serialization.
    """
    # OS-level identity
    os_username = _run(['whoami']) or os.environ.get('USER', '')
    env_email = os.environ.get('EMAIL', '')

    emails: Set[str] = set()
    display_name: Optional[str] = None

    if env_email and '@' in env_email:
        emails.add(env_email.lower())

    # gog-based identity (one account may return multiple emails)
    accounts = _discover_gog_accounts()
    for account in accounts:
        emails.add(account['email'].lower())
        person = _whoami_for_account(account)
        if person:
            if display_name is None:
                display_name = _extract_name(person)
            emails.update(_extract_emails(person))

    # Derive first name from display name
    first_name = ''
    if display_name:
        first_name = display_name.split()[0]
    elif os_username:
        first_name = os_username.split('.')[0].capitalize()

    return {
        'username': os_username,
        'emails': sorted(emails),
        'display_name': display_name or os_username,
        'first_name': first_name,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Discover user identity across sources (OS, gog CLI, environment)',
        epilog='Output: JSON with username, emails, display_name, first_name',
    )
    # No required args — all discovery is automatic
    parser.parse_args()

    try:
        identity = discover_self()
        print(json.dumps(identity, indent=2))
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
