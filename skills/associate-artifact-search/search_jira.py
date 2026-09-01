#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_jira.py

Query Jira for an associate's work products within a date range.
Reads credentials from acli config (~/.config/acli/jira_config.yaml) and macOS keychain.
Outputs JSON to stdout.

Usage:
    uv run search_jira.py --jira-id <account-id-or-email> \
        --since YYYY-MM-DD --until YYYY-MM-DD [--name "Display Name"]
"""

import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Optional


def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {s!r}. Expected YYYY-MM-DD.")


def get_creds() -> tuple[Optional[str], Optional[str]]:
    """Return (email, api_token) from acli config and macOS keychain."""
    email = None
    config = Path("~/.config/acli/jira_config.yaml").expanduser()
    try:
        for line in config.read_text().splitlines():
            if line.strip().startswith("email:"):
                email = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    token = None
    try:
        import subprocess
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "acli", "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            raw = r.stdout.strip()
            b64 = raw.removeprefix("go-keyring-base64:")
            token = base64.b64decode(b64 + "==").decode()
    except Exception:
        pass

    return email, token


def api(email: str, token: str, path: str, params: Optional[dict] = None) -> Optional[object]:
    base = "https://redhat.atlassian.net"
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"Jira API error ({path}): {exc}", file=sys.stderr)
        return None


def resolve_account_id(identifier: str, email: str, token: str) -> Optional[str]:
    """Return a Jira account ID from an email, display name, or existing account ID."""
    if ":" in identifier:
        return identifier  # already an account ID
    data = api(email, token, "/rest/api/3/user/search", {"query": identifier, "maxResults": 1})
    if isinstance(data, list) and data:
        return data[0].get("accountId")
    return None


def search(email: str, token: str, jql: str, max_results: int = 100) -> list[dict]:
    data = api(email, token, "/rest/api/3/search/jql", {
        "jql": jql,
        "fields": "key,summary,issuetype,status,updated,created",
        "expand": "changelog",
        "maxResults": max_results,
    })
    return data.get("issues", []) if isinstance(data, dict) else []


def fmt(issue: dict) -> dict:
    f = issue.get("fields", {})
    return {
        "key": issue["key"],
        "type": (f.get("issuetype") or {}).get("name"),
        "status": (f.get("status") or {}).get("name"),
        "summary": f.get("summary"),
        "created": (f.get("created") or "")[:10],
        "updated": (f.get("updated") or "")[:10],
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Search Jira for an associate's recent activity.")
    p.add_argument("--jira-id", required=True, help="Jira account ID or email address")
    p.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--name", help="Associate display name (metadata only)")
    args = p.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until)
    since_s, until_s = str(since), str(until)

    email, token = get_creds()
    if not email or not token:
        print(json.dumps({"error": "Could not load Jira credentials from acli config / keychain"}))
        sys.exit(1)

    account_id = resolve_account_id(args.jira_id, email, token)
    if not account_id:
        print(json.dumps({"error": f"Could not resolve Jira account: {args.jira_id!r}"}))
        sys.exit(1)

    # Issues created by this associate in the window
    created_raw = search(email, token,
        f'reporter = "{account_id}" AND created >= "{since_s}" AND created <= "{until_s}" '
        f'ORDER BY created DESC')

    # Issues assigned to them updated in the window — filter to changelog entries by them
    assigned_raw = search(email, token,
        f'assignee = "{account_id}" AND updated >= "{since_s}" AND updated <= "{until_s}" '
        f'ORDER BY updated DESC')

    # Status transitions driven by this associate
    status_changed_raw = search(email, token,
        f'status changed BY "{account_id}" AFTER "{since_s}" ORDER BY updated DESC')

    edited = []
    for issue in assigned_raw:
        histories = (issue.get("changelog") or {}).get("histories", [])
        user_histories = [
            h for h in histories
            if (h.get("author") or {}).get("accountId") == account_id
            and (h.get("created") or "")[:10] >= since_s
        ]
        if user_histories:
            entry = fmt(issue)
            entry["changes"] = [
                {
                    "date": h["created"][:10],
                    "fields": [item.get("field") for item in (h.get("items") or [])],
                }
                for h in user_histories
            ]
            edited.append(entry)

    created = [fmt(i) for i in created_raw]

    closed = []
    for issue in status_changed_raw:
        f = issue.get("fields", {})
        cat = ((f.get("status") or {}).get("statusCategory") or {}).get("key", "")
        if cat == "done":
            closed.append(fmt(issue))

    result = {
        "associate": {"name": args.name, "jira_account_id": account_id, "jira_id_input": args.jira_id},
        "timeframe": {"since": since_s, "until": until_s},
        "created": created,
        "closed": closed,
        "edited": edited,
        "summary": {
            "created": len(created),
            "closed": len(closed),
            "edited": len(edited),
        },
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
