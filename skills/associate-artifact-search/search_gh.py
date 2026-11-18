#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_gh.py

Query GitHub for an associate's activity within a date range.
Uses the authenticated `gh` CLI — no extra credentials needed.
Outputs JSON to stdout.

Two sources are combined:
  * `gh search prs` / `gh search commits` — covers every repo the authenticated
    user can read, including private and org-internal repos.
  * `gh api /users/<u>/events` — public-activity feed; supplements the above
    with issues, releases, branch creation, and push detail. It returns nothing
    for activity in private/internal repos.

Usage:
    uv run search_gh.py --github-user <username> \
        --since YYYY-MM-DD --until YYYY-MM-DD [--name "Display Name"]
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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


SEARCH_LIMIT = 200
MAX_VERIFY = 100  # cap on candidate PRs whose reviews/comments are fetched individually
PR_FIELDS = "number,title,repository,author,state,createdAt,updatedAt,closedAt,url,isDraft"


def gh_search(kind: str, flags: list[str], fields: str) -> Optional[list[dict]]:
    """Run `gh search <kind> <flags> --json <fields>`; return parsed list or None on failure."""
    cmd = ["gh", "search", kind, *flags, "--limit", str(SEARCH_LIMIT), "--json", fields]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"gh search {kind} error: {r.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(r.stdout or "[]")
    except Exception as exc:
        print(f"gh search {kind} exception: {exc}", file=sys.stderr)
        return None


def gh_pr_activity(repo: str, number: int) -> Optional[dict]:
    """Fetch a PR's reviews and issue comments (author + timestamps) via `gh pr view`."""
    try:
        r = subprocess.run(
            ["gh", "pr", "view", str(number), "--repo", repo, "--json", "reviews,comments"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            print(f"gh pr view {repo}#{number} error: {r.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(r.stdout or "{}")
    except Exception as exc:
        print(f"gh pr view {repo}#{number} exception: {exc}", file=sys.stderr)
        return None


def user_pr_activity(user: str, since_s: str, until_s: str, candidates: list[dict],
                     warnings: list[str]) -> tuple[list[dict], list[dict]]:
    """Verify candidates: keep PRs where `user` has a review/comment dated inside the window.

    `gh search --reviewed-by/--commenter --updated` matches PRs *updated by anyone* in the
    window, so it overstates the associate's activity; this checks authorship and date.
    Returns (reviewed_prs, commented_prs), each entry carrying the dated events.
    """
    if len(candidates) > MAX_VERIFY:
        warnings.append(f"{len(candidates)} candidate PRs; only the {MAX_VERIFY} most recently updated were verified")
        candidates = sorted(candidates, key=lambda e: e["updated"], reverse=True)[:MAX_VERIFY]

    def fetch(e: dict) -> Optional[dict]:
        return gh_pr_activity(e["repo"], e["number"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        activity = list(pool.map(fetch, candidates))

    reviewed, commented, failed = [], [], 0
    me = user.lower()
    for e, act in zip(candidates, activity):
        if act is None:
            failed += 1
            continue
        reviews = [
            {"date": (r.get("submittedAt") or "")[:10], "state": r.get("state")}
            for r in act.get("reviews") or []
            if ((r.get("author") or {}).get("login") or "").lower() == me
            and since_s <= (r.get("submittedAt") or "")[:10] <= until_s
        ]
        comments = [
            {"date": (c.get("createdAt") or "")[:10], "url": c.get("url")}
            for c in act.get("comments") or []
            if ((c.get("author") or {}).get("login") or "").lower() == me
            and since_s <= (c.get("createdAt") or "")[:10] <= until_s
        ]
        if reviews:
            reviewed.append({**e, "reviews": reviews})
        if comments:
            commented.append({**e, "comments": comments})
    if failed:
        warnings.append(f"could not verify {failed} candidate PR(s); their reviews/comments are omitted")
    return reviewed, commented


def fmt_search_pr(pr: dict, **extra) -> dict:
    out = {
        "repo": (pr.get("repository") or {}).get("nameWithOwner"),
        "number": pr.get("number"),
        "title": (pr.get("title") or "")[:120],
        "author": (pr.get("author") or {}).get("login"),
        "state": pr.get("state"),
        "draft": bool(pr.get("isDraft")),
        "created": (pr.get("createdAt") or "")[:10],
        "updated": (pr.get("updatedAt") or "")[:10],
        "closed": (pr.get("closedAt") or "")[:10] or None,
        "url": pr.get("url"),
    }
    out.update(extra)
    return out


def pr_key(pr: dict) -> tuple:
    return (pr.get("repo"), pr.get("number"))


def search_activity(user: str, since_s: str, until_s: str, warnings: list[str]) -> Optional[dict]:
    """Collect PRs and commits via `gh search`. Returns None if every search failed."""
    rng = f"{since_s}..{until_s}"
    results = {
        "created": gh_search("prs", ["--author", user, "--created", rng], PR_FIELDS),
        "merged": gh_search("prs", ["--author", user, "--merged-at", rng], PR_FIELDS),
        "reviewed": gh_search("prs", ["--reviewed-by", user, "--updated", rng], PR_FIELDS),
        "commented": gh_search("prs", ["--commenter", user, "--updated", rng], PR_FIELDS),
        "commits": gh_search("commits", ["--author", user, "--committer-date", rng],
                             "repository,sha,commit,url"),
    }
    failed = [k for k, v in results.items() if v is None]
    if len(failed) == len(results):
        return None
    for k in failed:
        warnings.append(f"gh search ({k}) failed; that category may be incomplete")
    for k, v in results.items():
        if v is not None and len(v) >= SEARCH_LIMIT:
            warnings.append(f"gh search ({k}) hit the {SEARCH_LIMIT}-result limit; results are truncated")

    # Authored PRs: union of created-in-window and merged-in-window, de-duplicated.
    authored: dict[tuple, dict] = {}
    for pr in results["created"] or []:
        e = fmt_search_pr(pr, created_in_window=True, merged_in_window=False)
        authored[pr_key(e)] = e
    for pr in results["merged"] or []:
        e = fmt_search_pr(pr, created_in_window=False, merged_in_window=True)
        if pr_key(e) in authored:
            authored[pr_key(e)]["merged_in_window"] = True
        else:
            authored[pr_key(e)] = e

    def others(rows: Optional[list[dict]]) -> list[dict]:
        # Reviewed/commented lists include the associate's own PRs; keep only others' PRs.
        out = [fmt_search_pr(pr) for pr in rows or []]
        return [e for e in out if (e["author"] or "").lower() != user.lower()]

    candidates: dict[tuple, dict] = {}
    for e in others(results["reviewed"]) + others(results["commented"]):
        candidates[pr_key(e)] = e
    reviewed_prs, commented_prs = user_pr_activity(
        user, since_s, until_s, list(candidates.values()), warnings)

    commits = []
    for c in results["commits"] or []:
        commit = c.get("commit") or {}
        committer = commit.get("committer") or {}
        commits.append({
            "repo": (c.get("repository") or {}).get("fullName") or (c.get("repository") or {}).get("nameWithOwner"),
            "sha": (c.get("sha") or "")[:8],
            "date": (committer.get("date") or "")[:10],
            "message": (commit.get("message") or "").split("\n")[0][:120],
            "url": c.get("url"),
        })

    return {
        "authored_prs": sorted(authored.values(), key=lambda e: e["created"], reverse=True),
        "reviewed_prs": reviewed_prs,
        "commented_prs": commented_prs,
        "commits": commits,
    }


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

    warnings: list[str] = []
    searched = search_activity(args.github_user, since_s, until_s, warnings)
    events = gh_events(args.github_user)
    if events is None and searched is None:
        print(json.dumps({"error": f"Failed to fetch activity for GitHub user: {args.github_user!r}"}))
        sys.exit(1)
    if events is None:
        warnings.append("events feed unavailable; pushes/issues/releases/creates may be incomplete")
        events = []
    if searched is None:
        warnings.append("gh search unavailable; private/internal repo activity is not covered")
        searched = {"authored_prs": [], "reviewed_prs": [], "commented_prs": [], "commits": []}

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

    repos_touched = sorted(
        {(e.get("repo") or {}).get("name", "?") for e in recent if e.get("repo")}
        | {x["repo"] for k in ("authored_prs", "reviewed_prs", "commented_prs", "commits")
           for x in searched[k] if x.get("repo")}
    )
    # Unique commits across the events feed (push payloads) and commit search, by short SHA.
    unique_commits = {c["sha"] for c in pushes} | {c["sha"] for c in searched["commits"]}

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
        "authored_prs": searched["authored_prs"],
        "reviewed_prs": searched["reviewed_prs"],
        "commented_prs": searched["commented_prs"],
        "commits": searched["commits"],
        "warnings": warnings,
        "summary": {
            "total_events": len(recent),
            "commits": len(unique_commits),
            "authored_prs": len(searched["authored_prs"]),
            "authored_prs_merged_in_window": sum(
                1 for x in searched["authored_prs"] if x["merged_in_window"]),
            "reviewed_prs": len(searched["reviewed_prs"]),
            "commented_prs": len(searched["commented_prs"]),
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
