#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_gh.py

Query GitHub for an associate's public activity within a date range.
Uses the authenticated `gh` CLI — no extra credentials needed.
Outputs JSON to stdout.

Usage:
    uv run search_gh.py --github-user <username> \
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


def gh_events(username: str) -> Optional[list[dict]]:
    try:
        r = subprocess.run(
            ["gh", "api", f"/users/{username}/events", "--paginate"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            print(f"gh api error: {r.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except Exception as exc:
        print(f"gh api exception: {exc}", file=sys.stderr)
        return None


def main() -> None:
    p = argparse.ArgumentParser(description="Search GitHub for an associate's recent activity.")
    p.add_argument("--github-user", required=True, help="GitHub username")
    p.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--name", help="Associate display name (metadata only)")
    args = p.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until)
    since_s, until_s = str(since), str(until)

    events = gh_events(args.github_user)
    if events is None:
        print(json.dumps({"error": f"Failed to fetch events for GitHub user: {args.github_user!r}"}))
        sys.exit(1)

    recent = [
        e for e in events
        if since_s <= (e.get("created_at") or "")[:10] <= until_s
    ]

    pushes: list[dict] = []
    pull_requests: list[dict] = []
    pr_reviews: list[dict] = []
    issues: list[dict] = []
    issue_comments: list[dict] = []
    releases: list[dict] = []
    creates: list[dict] = []

    for e in recent:
        etype = e["type"]
        repo = (e.get("repo") or {}).get("name", "?")
        payload = e.get("payload") or {}
        event_date = (e.get("created_at") or "")[:10]

        if etype == "PushEvent":
            branch = payload.get("ref", "").removeprefix("refs/heads/")
            for commit in payload.get("commits") or []:
                pushes.append({
                    "date": event_date,
                    "repo": repo,
                    "branch": branch,
                    "sha": (commit.get("sha") or "")[:8],
                    "message": (commit.get("message") or "").split("\n")[0][:120],
                })

        elif etype == "PullRequestEvent":
            pr = payload.get("pull_request") or {}
            pull_requests.append({
                "date": event_date,
                "repo": repo,
                "action": payload.get("action"),
                "number": pr.get("number"),
                "title": (pr.get("title") or "")[:120],
                "merged": bool(pr.get("merged")),
                "url": pr.get("html_url"),
            })

        elif etype == "PullRequestReviewEvent":
            pr = payload.get("pull_request") or {}
            pr_reviews.append({
                "date": event_date,
                "repo": repo,
                "state": (payload.get("review") or {}).get("state"),
                "number": pr.get("number"),
                "title": (pr.get("title") or "")[:120],
                "url": pr.get("html_url"),
            })

        elif etype == "IssuesEvent":
            issue = payload.get("issue") or {}
            issues.append({
                "date": event_date,
                "repo": repo,
                "action": payload.get("action"),
                "number": issue.get("number"),
                "title": (issue.get("title") or "")[:120],
                "url": issue.get("html_url"),
            })

        elif etype == "IssueCommentEvent":
            issue = payload.get("issue") or {}
            issue_comments.append({
                "date": event_date,
                "repo": repo,
                "number": issue.get("number"),
                "title": (issue.get("title") or "")[:120],
                "url": issue.get("html_url"),
            })

        elif etype == "ReleaseEvent":
            release = payload.get("release") or {}
            releases.append({
                "date": event_date,
                "repo": repo,
                "tag": release.get("tag_name"),
                "name": (release.get("name") or "")[:80],
                "url": release.get("html_url"),
            })

        elif etype == "CreateEvent":
            creates.append({
                "date": event_date,
                "repo": repo,
                "ref_type": payload.get("ref_type"),
                "ref": payload.get("ref") or "",
            })

    repos_touched = sorted({(e.get("repo") or {}).get("name", "?") for e in recent if e.get("repo")})

    result = {
        "associate": {"name": args.name, "github_username": args.github_user},
        "timeframe": {"since": since_s, "until": until_s},
        "pushes": pushes,
        "pull_requests": pull_requests,
        "pr_reviews": pr_reviews,
        "issues": issues,
        "issue_comments": issue_comments,
        "releases": releases,
        "creates": creates,
        "summary": {
            "total_events": len(recent),
            "commits": len(pushes),
            "pull_requests_opened": sum(1 for p in pull_requests if p["action"] == "opened"),
            "pull_requests_merged": sum(1 for p in pull_requests if p["merged"]),
            "pr_reviews": len(pr_reviews),
            "issues_closed": sum(1 for i in issues if i["action"] == "closed"),
            "issue_comments": len(issue_comments),
            "releases": len(releases),
            "repos_touched": repos_touched,
        },
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
