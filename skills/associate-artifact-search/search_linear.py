#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_linear.py

Query Linear (GraphQL) for an associate's work products within a date range.
Read-only. Reads a personal API key from the LINEAR_API_KEY environment variable.
Outputs JSON to stdout.

Usage:
    uv run search_linear.py --linear-user <email-or-user-id-or-name> \
        --since YYYY-MM-DD --until YYYY-MM-DD [--name "Display Name"]

Auth: create a personal API key at Linear > Settings > Security & access and
export it as LINEAR_API_KEY. The key is sent as-is in the Authorization header
(no "Bearer" prefix).
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from typing import Optional

API_URL = "https://api.linear.app/graphql"
PAGE_SIZE = 50
MAX_PAGES = 10
COMMENT_TRUNCATE = 300
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {s!r}. Expected YYYY-MM-DD.")


class LinearError(Exception):
    pass


def graphql(api_key: str, query: str, variables: Optional[dict] = None) -> dict:
    """POST a GraphQL query and return the `data` object. Raises LinearError on failure."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": api_key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # GraphQL validation errors come back as HTTP 400 with the details in the body.
        try:
            payload = json.loads(exc.read())
        except Exception:
            raise LinearError(f"Linear API request failed: {exc}") from exc
        if not payload.get("errors"):
            raise LinearError(f"Linear API request failed: {exc}") from exc
    except Exception as exc:
        raise LinearError(f"Linear API request failed: {exc}") from exc
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", "unknown error") for e in payload["errors"])
        raise LinearError(f"Linear GraphQL error: {msgs}")
    return payload.get("data") or {}


def paginate(api_key: str, query: str, variables: dict, connection: str) -> list[dict]:
    """Follow Relay-style pagination for `connection` and return all nodes (capped)."""
    nodes: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        data = graphql(api_key, query, {**variables, "after": cursor})
        conn = data.get(connection) or {}
        nodes.extend(conn.get("nodes") or [])
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
    return nodes


# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

USER_FIELDS = "id name displayName email"


def resolve_user(identifier: str, api_key: str) -> Optional[dict]:
    """Resolve an email, user UUID, or name to a Linear user dict (or None)."""
    if UUID_RE.match(identifier):
        data = graphql(api_key, f"query($id: String!) {{ user(id: $id) {{ {USER_FIELDS} }} }}",
                       {"id": identifier})
        return data.get("user")
    if "@" in identifier:
        filt = {"email": {"eq": identifier}}
    else:
        filt = {"or": [{"name": {"containsIgnoreCase": identifier}},
                       {"displayName": {"containsIgnoreCase": identifier}}]}
    data = graphql(
        api_key,
        f"query($filter: UserFilter) {{ users(filter: $filter, first: 2) {{ nodes {{ {USER_FIELDS} }} }} }}",
        {"filter": filt},
    )
    nodes = (data.get("users") or {}).get("nodes") or []
    return nodes[0] if nodes else None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

ISSUE_FIELDS = """
  identifier title url branchName priority
  createdAt updatedAt startedAt completedAt canceledAt
  state { name type }
  team { key }
  project { name }
  cycle { number }
  labels { nodes { name } }
  attachments(first: 5) { nodes { url sourceType title } }
"""

ISSUES_QUERY = f"""
query($filter: IssueFilter, $after: String) {{
  issues(filter: $filter, first: {PAGE_SIZE}, after: $after, orderBy: updatedAt) {{
    nodes {{ {ISSUE_FIELDS} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

# Same as ISSUES_QUERY plus workflow history, used to find status transitions.
ISSUES_WITH_HISTORY_QUERY = f"""
query($filter: IssueFilter, $after: String) {{
  issues(filter: $filter, first: {PAGE_SIZE}, after: $after, orderBy: updatedAt) {{
    nodes {{
      {ISSUE_FIELDS}
      history(first: 25) {{
        nodes {{
          createdAt
          actor {{ id }}
          fromState {{ name }}
          toState {{ name type }}
        }}
      }}
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

COMMENTS_QUERY = f"""
query($filter: CommentFilter, $after: String) {{
  comments(filter: $filter, first: {PAGE_SIZE}, after: $after) {{
    nodes {{
      createdAt body url
      issue {{ identifier title url state {{ name type }} }}
      documentContent {{
        issue {{ identifier title url state {{ name type }} }}
        document {{ title url }}
        project {{ name url }}
      }}
      project {{ name url }}
      projectUpdate {{ url project {{ name }} }}
      initiative {{ name url }}
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""


def window(since: date, until: date) -> dict:
    """Inclusive UTC bounds for the date window."""
    return {"gte": f"{since}T00:00:00.000Z", "lte": f"{until}T23:59:59.999Z"}


def fmt_issue(issue: dict) -> dict:
    return {
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "team": (issue.get("team") or {}).get("key"),
        "status": (issue.get("state") or {}).get("name"),
        "status_type": (issue.get("state") or {}).get("type"),
        "project": (issue.get("project") or {}).get("name"),
        "cycle": (issue.get("cycle") or {}).get("number"),
        "labels": [l["name"] for l in (issue.get("labels") or {}).get("nodes", [])],
        "branch": issue.get("branchName"),
        "created": (issue.get("createdAt") or "")[:10],
        "updated": (issue.get("updatedAt") or "")[:10],
        "completed": (issue.get("completedAt") or "")[:10] or None,
        "linked": [
            {"url": a.get("url"), "source": a.get("sourceType"), "title": a.get("title")}
            for a in (issue.get("attachments") or {}).get("nodes", [])
        ],
    }


def status_changes_by(issues: list[dict], user_id: str, since_s: str, until_s: str) -> list[dict]:
    """Issues with workflow-state transitions made by the user inside the window."""
    out = []
    for issue in issues:
        events = []
        for h in (issue.get("history") or {}).get("nodes", []):
            if not h.get("toState"):
                continue
            if (h.get("actor") or {}).get("id") != user_id:
                continue
            day = (h.get("createdAt") or "")[:10]
            if not (since_s <= day <= until_s):
                continue
            events.append({
                "date": day,
                "from": (h.get("fromState") or {}).get("name"),
                "to": h["toState"].get("name"),
                "to_type": h["toState"].get("type"),
            })
        if events:
            entry = fmt_issue(issue)
            entry["transitions"] = events
            out.append(entry)
    return out


def fmt_comment(c: dict) -> dict:
    body = (c.get("body") or "").strip()
    if len(body) > COMMENT_TRUNCATE:
        body = body[:COMMENT_TRUNCATE] + "…"
    content = c.get("documentContent") or {}
    # Inline comments on an issue description carry the issue via documentContent.
    issue = c.get("issue") or content.get("issue") or {}
    context, title, url = None, None, None
    if issue:
        context, title, url = "issue", issue.get("title"), issue.get("url")
    elif content.get("document"):
        context, title, url = "document", content["document"].get("title"), content["document"].get("url")
    elif c.get("projectUpdate"):
        pu = c["projectUpdate"]
        context, title, url = "project_update", (pu.get("project") or {}).get("name"), pu.get("url")
    elif c.get("project") or content.get("project"):
        proj = c.get("project") or content.get("project")
        context, title, url = "project", proj.get("name"), proj.get("url")
    elif c.get("initiative"):
        context, title, url = "initiative", c["initiative"].get("name"), c["initiative"].get("url")
    return {
        "date": (c.get("createdAt") or "")[:10],
        "url": c.get("url"),
        "context": context,
        "context_title": title,
        "context_url": url,
        "issue": issue.get("identifier"),
        "issue_title": issue.get("title"),
        "issue_url": issue.get("url"),
        "issue_status": (issue.get("state") or {}).get("name"),
        "body": body,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Search Linear for an associate's recent activity.")
    p.add_argument("--linear-user", required=True, help="Linear user email, user ID (UUID), or name")
    p.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--name", help="Associate display name (metadata only)")
    args = p.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until)
    since_s, until_s = str(since), str(until)

    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        print(json.dumps({"error": "LINEAR_API_KEY is not set. Create a personal API key at "
                                   "Linear > Settings > Security & access."}))
        sys.exit(1)

    try:
        user = resolve_user(args.linear_user, api_key)
        if not user:
            print(json.dumps({"error": f"Could not resolve Linear user: {args.linear_user!r}",
                              "hint": "Try the user's name or Linear user ID instead of email."}))
            sys.exit(1)
        uid = user["id"]
        win = window(since, until)

        created_raw = paginate(api_key, ISSUES_QUERY, {"filter": {
            "creator": {"id": {"eq": uid}}, "createdAt": win}}, "issues")
        completed_raw = paginate(api_key, ISSUES_QUERY, {"filter": {
            "assignee": {"id": {"eq": uid}}, "completedAt": win}}, "issues")
        assigned_raw = paginate(api_key, ISSUES_WITH_HISTORY_QUERY, {"filter": {
            "assignee": {"id": {"eq": uid}}, "updatedAt": win}}, "issues")
        comments_raw = paginate(api_key, COMMENTS_QUERY, {"filter": {
            "user": {"id": {"eq": uid}}, "createdAt": win}}, "comments")
    except LinearError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)

    created = [fmt_issue(i) for i in created_raw]
    completed = [fmt_issue(i) for i in completed_raw]
    updated = [fmt_issue(i) for i in assigned_raw]
    status_changes = status_changes_by(assigned_raw, uid, since_s, until_s)
    comments = [fmt_comment(c) for c in comments_raw]

    result = {
        "associate": {
            "name": args.name or user.get("name"),
            "linear_user_id": uid,
            "linear_email": user.get("email"),
            "linear_display_name": user.get("displayName"),
            "linear_user_input": args.linear_user,
        },
        "timeframe": {"since": since_s, "until": until_s},
        "created": created,
        "completed": completed,
        "updated": updated,
        "status_changes": status_changes,
        "comments": comments,
        "summary": {
            "created": len(created),
            "completed": len(completed),
            "updated": len(updated),
            "status_changes": len(status_changes),
            "comments": len(comments),
        },
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
