#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_gog.py

Search Google Drive (Docs, Sheets, Slides, Forms) and Gmail for activity
by one or more users within a date range. Uses the `gog` CLI for all API
calls — no extra credentials or Python Google libraries needed.

Authentication: whatever account `gog` is configured for (run `gog auth`
to set up). To target a specific account use --account <email>.

Drive results include files owned by the target user(s). Gmail results
include threads sent from or to the target user found in the authenticated
user's mailbox.

Outputs JSON (default) or markdown to stdout.

Usage:
    uv run search_gog.py --email <user@domain.com> \
        --since YYYY-MM-DD --until YYYY-MM-DD [--name "Display Name"] \
        [--account <your-gog-account>] [--skip-drive] [--skip-gmail]

    uv run search_gog.py --email <user1@domain.com> --email <user2@domain.com> \
        --since YYYY-MM-DD --until YYYY-MM-DD \
        --type doc slide --output markdown --skip-gmail
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Optional


WORKSPACE_MIMES = {
    "application/vnd.google-apps.document": "Doc",
    "application/vnd.google-apps.spreadsheet": "Sheet",
    "application/vnd.google-apps.presentation": "Slide",
    "application/vnd.google-apps.form": "Form",
}

TYPE_TO_MIME = {v.lower(): k for k, v in WORKSPACE_MIMES.items()}

DRIVE_FIELDS = (
    "files(id,name,mimeType,modifiedTime,createdTime,webViewLink,"
    "lastModifyingUser,owners,sharingUser),nextPageToken"
)

DEFAULT_EXCLUDE_PATTERNS = [
    r"Weekly Status",
    r"Notes by Gemini$",
    r" - Status: Week ending",
]


def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {s!r}. Expected YYYY-MM-DD.")


def run_gog(cmd: list[str], account: Optional[str] = None) -> Optional[dict]:
    """Run a gog command and return parsed JSON output, or None on failure."""
    base = ["gog", "--json"]
    if account:
        base += ["--account", account]
    full = base + cmd
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"gog error ({' '.join(cmd[:3])}): {r.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except Exception as exc:
        print(f"gog exception ({' '.join(cmd[:3])}): {exc}", file=sys.stderr)
        return None


def resolve_display_name(email: str, account: Optional[str] = None) -> str:
    """Look up a display name for an email via gog people search."""
    resp = run_gog(["people", "search", email], account)
    if resp and isinstance(resp, dict):
        people = resp.get("people", [])
        if people:
            name = people[0].get("name", "")
            if name:
                return name
    return email.split("@")[0]


def fetch_drive_page(query: str, page_token: Optional[str], account: Optional[str]) -> Optional[dict]:
    cmd = [
        "drive", "ls",
        "--all",
        "--all-drives",
        f"--query={query}",
        f"--fields={DRIVE_FIELDS}",
        "--max=200",
    ]
    if page_token:
        cmd.append(f"--page={page_token}")
    return run_gog(cmd, account)


def search_drive(
    associate_email: str,
    since: date,
    until: date,
    account: Optional[str],
    types: Optional[list[str]] = None,
    exclude_patterns: Optional[list[re.Pattern]] = None,
) -> dict:
    """
    Find Workspace files owned by the target user in the given date range.
    Uses server-side owner filtering for cross-domain visibility.
    """
    if types:
        mimes = [TYPE_TO_MIME[t] for t in types if t in TYPE_TO_MIME]
    else:
        mimes = list(WORKSPACE_MIMES.keys())

    mime_clause = " or ".join(f"mimeType='{m}'" for m in mimes)
    since_ts = f"{since}T00:00:00"
    until_ts = f"{until}T23:59:59"
    query = (
        f"'{associate_email}' in owners"
        f" and ({mime_clause})"
        f" and modifiedTime >= '{since_ts}'"
        f" and modifiedTime <= '{until_ts}'"
        f" and trashed = false"
    )

    all_files: list[dict] = []
    page_token: Optional[str] = None

    while True:
        resp = fetch_drive_page(query, page_token, account)
        if resp is None:
            break
        for f in resp.get("files", []):
            title = f.get("name", "")
            if exclude_patterns and any(p.search(title) for p in exclude_patterns):
                continue
            last_modifier = (f.get("lastModifyingUser") or {}).get("emailAddress", "")
            owner_emails = [o.get("emailAddress", "") for o in (f.get("owners") or [])]
            all_files.append({
                "id": f.get("id"),
                "name": title,
                "type": WORKSPACE_MIMES.get(f.get("mimeType", ""), f.get("mimeType", "")),
                "url": f.get("webViewLink"),
                "modified": (f.get("modifiedTime") or "")[:10],
                "created": (f.get("createdTime") or "")[:10],
                "owned_by_associate": associate_email in owner_emails,
                "last_modified_by_associate": associate_email == last_modifier,
            })
        page_token = resp.get("nextPageToken") or None
        if not page_token:
            break

    by_type: dict[str, int] = {}
    for f in all_files:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1

    return {
        "files": all_files,
        "summary": {
            "total": len(all_files),
            "by_type": by_type,
            "owned_by_associate": sum(1 for f in all_files if f["owned_by_associate"]),
            "last_modified_by_associate": sum(1 for f in all_files if f["last_modified_by_associate"]),
        },
    }


def search_gmail_query(query: str, account: Optional[str]) -> list[dict]:
    """Run a gog gmail search and return all threads, paginating."""
    threads: list[dict] = []
    page_token: Optional[str] = None

    while True:
        cmd = ["gmail", "search", query, "--max=100", "--all"]
        if page_token:
            cmd.append(f"--page={page_token}")
        resp = run_gog(cmd, account)
        if resp is None:
            break
        for t in resp.get("threads", []):
            threads.append({
                "id": t.get("id"),
                "thread_id": t.get("id"),
                "date": t.get("date", ""),
                "from": t.get("from", ""),
                "subject": t.get("subject", ""),
                "labels": t.get("labels", []),
                "message_count": t.get("messageCount", 1),
            })
        page_token = resp.get("nextPageToken") or None
        if not page_token:
            break

    return threads


def search_gmail(associate_email: str, since: date, until: date, account: Optional[str]) -> dict:
    """
    Search the authenticated user's Gmail for threads involving the target user.
    """
    since_fmt = since.strftime("%Y/%m/%d")
    until_fmt = until.strftime("%Y/%m/%d")

    sent_by = search_gmail_query(
        f"from:{associate_email} after:{since_fmt} before:{until_fmt}", account
    )
    sent_to = search_gmail_query(
        f"to:{associate_email} after:{since_fmt} before:{until_fmt}", account
    )

    seen: set[str] = set()
    all_threads: list[dict] = []
    for t in sent_by + sent_to:
        tid = t.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            all_threads.append(t)

    return {
        "sent_by_associate": sent_by,
        "sent_to_associate": sent_to,
        "summary": {
            "sent_by_associate": len(sent_by),
            "sent_to_associate": len(sent_to),
            "unique_threads": len(seen),
        },
    }


def format_markdown(results: list[dict]) -> str:
    """Format multi-user Drive results as markdown grouped by month and author."""
    by_month: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in results:
        author = r["name"]
        for f in r.get("drive", {}).get("files", []):
            month = f["modified"][:7] if f["modified"] else "unknown"
            by_month[month][author].append(f)

    lines: list[str] = []
    for month in sorted(by_month.keys(), reverse=True):
        lines.append(f"# {month}")
        for author in sorted(by_month[month].keys()):
            lines.append(f"## {author}")
            items = sorted(
                by_month[month][author],
                key=lambda x: x["modified"],
                reverse=True,
            )
            for f in items:
                lines.append(
                    f"* [{f['name']}]({f['url']})"
                    f" (created: {f['created']}, modified: {f['modified']})"
                )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Search Google Drive and Gmail for user activity via the gog CLI."
    )
    p.add_argument(
        "--email", action="append", required=True,
        help="Target user's email address (repeatable for multiple users)",
    )
    p.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    p.add_argument(
        "--name", action="append",
        help="Display name for corresponding --email (same order, repeatable)",
    )
    p.add_argument("--account", help="gog account email or alias to authenticate as")
    p.add_argument("--skip-drive", action="store_true", help="Skip Google Drive search")
    p.add_argument("--skip-gmail", action="store_true", help="Skip Gmail search")
    p.add_argument(
        "--type", nargs="+", choices=["doc", "sheet", "slide", "form"],
        default=None, help="Filter Drive results by document type",
    )
    p.add_argument(
        "--output", choices=["json", "markdown"], default="json",
        help="Output format (default: json)",
    )
    p.add_argument(
        "--exclude", nargs="*", default=None,
        help="Regex patterns to exclude by title (default: built-in patterns)",
    )
    args = p.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until)

    if args.exclude is not None:
        exclude_patterns = [re.compile(p) for p in args.exclude] if args.exclude else []
    else:
        exclude_patterns = [re.compile(p) for p in DEFAULT_EXCLUDE_PATTERNS]

    names = args.name or []
    all_results: list[dict] = []

    for i, email in enumerate(args.email):
        if i < len(names):
            display_name = names[i]
        else:
            display_name = resolve_display_name(email, args.account)
        print(f"Searching {display_name} ({email})...", file=sys.stderr)

        result: dict = {
            "name": display_name,
            "email": email,
            "timeframe": {"since": str(since), "until": str(until)},
        }

        if not args.skip_drive:
            result["drive"] = search_drive(
                email, since, until, args.account, args.type, exclude_patterns
            )
            drive_total = result["drive"].get("summary", {}).get("total", 0)
            print(f"  Drive: {drive_total} files", file=sys.stderr)

        if not args.skip_gmail:
            result["gmail"] = search_gmail(email, since, until, args.account)
            gmail_total = result["gmail"].get("summary", {}).get("unique_threads", 0)
            print(f"  Gmail: {gmail_total} threads", file=sys.stderr)

        all_results.append(result)

    if args.output == "markdown":
        print(format_markdown(all_results))
    elif len(all_results) == 1:
        # Backward-compatible: single user returns flat JSON
        r = all_results[0]
        output = {
            "associate": {"name": r["name"], "email": r["email"]},
            "timeframe": r["timeframe"],
        }
        if "drive" in r:
            output["drive"] = r["drive"]
        if "gmail" in r:
            output["gmail"] = r["gmail"]
        print(json.dumps(output, indent=2, default=str))
    else:
        print(json.dumps(all_results, indent=2, default=str))


if __name__ == "__main__":
    main()
