#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_glab.py

Query GitLab for an associate's activity within a date range.
Uses the authenticated `glab` CLI — no extra credentials needed.
Outputs JSON to stdout.

Usage:
    uv run search_glab.py --gitlab-user <username> \
        --since YYYY-MM-DD --until YYYY-MM-DD [--name "Display Name"]
"""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from typing import Optional


def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {s!r}. Expected YYYY-MM-DD.")


def glab_events(username: str, page_size: int = 100) -> Optional[list[dict]]:
    """Fetch user events via glab, paging until events fall before the since cutoff."""
    try:
        r = subprocess.run(
            ["glab", "api", f"users/{username}/events?per_page={page_size}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            print(f"glab api error: {r.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except Exception as exc:
        print(f"glab api exception: {exc}", file=sys.stderr)
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Search GitLab for an associate's recent activity.")
    p.add_argument("--gitlab-user", required=True, help="GitLab username")
    p.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--name", help="Associate display name (metadata only)")
    args = p.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until)
    since_s, until_s = str(since), str(until)

    events = glab_events(args.gitlab_user)
    if events is None:
        print(json.dumps({"error": f"Failed to fetch events for GitLab user: {args.gitlab_user!r}"}))
        sys.exit(1)

    recent = [
        e for e in events
        if since_s <= (e.get("created_at") or "")[:10] <= until_s
    ]

    # Group events by action type with structured detail
    by_action: dict[str, list[dict]] = {}
    for e in recent:
        action = e.get("action_name", "unknown")
        entry: dict = {
            "date": (e.get("created_at") or "")[:10],
            "action": action,
            "target_type": e.get("target_type"),
            "target_title": str(e.get("target_title") or "")[:120],
            "project_id": e.get("project_id"),
        }
        note = e.get("note")
        if isinstance(note, dict):
            entry["comment_body"] = str(note.get("body") or "")[:300]
        push = e.get("push_data")
        if isinstance(push, dict):
            entry["push_data"] = {
                "branch": push.get("ref"),
                "commit_title": str(push.get("commit_title") or "")[:120],
                "commit_count": push.get("commit_count"),
            }
        by_action.setdefault(action, []).append(entry)

    last_activity = (events[0].get("created_at") or "")[:10] if events else None

    result = {
        "associate": {"name": args.name, "gitlab_username": args.gitlab_user},
        "timeframe": {"since": since_s, "until": until_s},
        "events": recent,
        "by_action": by_action,
        "summary": {
            "total_events": len(recent),
            "actions": {k: len(v) for k, v in by_action.items()},
            "last_activity_overall": last_activity,
        },
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
