#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Discover Jira items that need attention — stale assigned issues and
follow-up items on issues you created.

Reads credentials from acli config (~/.config/acli/jira_config.yaml)
and macOS keychain.  Outputs JSON to stdout.

Usage:
    python3 discover_jira_actions.py \
        --self-json ~/.cache/obsidian-claude-plugins/self.json \
        --projects FM,OME,ACM \
        --stale-days 7
"""

import argparse
import base64
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional


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
    """Make an authenticated GET request to Jira REST API."""
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
        return identifier
    data = api(email, token, "/rest/api/3/user/search", {"query": identifier, "maxResults": 1})
    if isinstance(data, list) and data:
        return data[0].get("accountId")
    return None


def search(email: str, token: str, jql: str, max_results: int = 100) -> list[dict]:
    """Run a JQL search and return the issues list."""
    data = api(email, token, "/rest/api/3/search/jql", {
        "jql": jql,
        "fields": "key,summary,issuetype,status,updated,created,assignee",
        "maxResults": max_results,
    })
    return data.get("issues", []) if isinstance(data, dict) else []


def fmt_issue(issue: dict, include_assignee: bool = False) -> dict:
    """Format a raw Jira issue into a clean dict for output."""
    f = issue.get("fields", {})
    updated_str = (f.get("updated") or "")[:10]
    days_since = 0
    if updated_str:
        try:
            updated_date = datetime.strptime(updated_str, "%Y-%m-%d")
            days_since = (datetime.now() - updated_date).days
        except ValueError:
            pass

    entry = {
        "key": issue["key"],
        "summary": f.get("summary"),
        "type": (f.get("issuetype") or {}).get("name"),
        "status": (f.get("status") or {}).get("name"),
        "days_since_update": days_since,
        "updated": updated_str,
        "url": f"https://redhat.atlassian.net/browse/{issue['key']}",
    }
    if include_assignee:
        assignee = f.get("assignee") or {}
        entry["assignee"] = assignee.get("displayName", "Unassigned")
    return entry


def main() -> None:
    p = argparse.ArgumentParser(description="Discover Jira items that need attention.")
    p.add_argument("--self-json", required=True, help="Path to discover_self.py JSON output (unused, reserved for future)")
    p.add_argument("--projects", required=True, help="Comma-separated Jira project keys")
    p.add_argument("--stale-days", type=int, default=7, help="Days without update to consider stale (default: 7)")
    args = p.parse_args()

    projects = [k.strip() for k in args.projects.split(",") if k.strip()]
    if not projects:
        print(json.dumps({"error": "No project keys provided"}))
        sys.exit(1)

    email, token = get_creds()
    if not email or not token:
        print(json.dumps({"error": "Could not load Jira credentials from acli config / keychain"}))
        sys.exit(1)

    account_id = resolve_account_id(email, email, token)
    if not account_id:
        print(json.dumps({"error": f"Could not resolve Jira account for {lookup_email}"}))
        sys.exit(1)

    project_clause = "(" + ", ".join(projects) + ")"
    stale_days = args.stale_days

    stale_assigned = search(email, token,
        f'assignee = "{account_id}" AND project in {project_clause} '
        f'AND statusCategory != Done AND updated <= -{stale_days}d '
        f'ORDER BY updated ASC')

    stale_created = search(email, token,
        f'reporter = "{account_id}" AND assignee != "{account_id}" '
        f'AND project in {project_clause} '
        f'AND statusCategory != Done AND updated <= -{stale_days}d '
        f'ORDER BY updated ASC')

    stale_assigned_out = [fmt_issue(i) for i in stale_assigned]
    stale_created_out = [fmt_issue(i, include_assignee=True) for i in stale_created]

    result = {
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "projects": projects,
        "stale_days": stale_days,
        "actions": {
            "stale_assigned": stale_assigned_out,
            "stale_created_followup": stale_created_out,
        },
        "counts": {
            "stale_assigned": len(stale_assigned_out),
            "stale_created_followup": len(stale_created_out),
            "total": len(stale_assigned_out) + len(stale_created_out),
        },
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
