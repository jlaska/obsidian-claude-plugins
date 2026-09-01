#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
search_gog.py

Search Google Drive (Docs, Sheets, Slides, Forms) and Gmail for an associate's
contributions within a date range. Uses the `gog` CLI for all API calls — no
extra credentials or Python Google libraries needed.

Authentication: whatever account `gog` is configured for (run `gog auth` to set up).
To target a specific account use --account <email> (passed through to gog).

Drive results include files the associate owns or last modified that are visible
to the authenticated user. Gmail results include threads sent from or to the
associate found in the authenticated user's mailbox.

Outputs JSON to stdout.

Usage:
    uv run search_gog.py --email <associate@domain.com> \
        --since YYYY-MM-DD --until YYYY-MM-DD [--name "Display Name"] \
        [--account <your-gog-account>] [--skip-drive] [--skip-gmail]
"""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from typing import Optional


WORKSPACE_MIMES = {
    "application/vnd.google-apps.document": "Doc",
    "application/vnd.google-apps.spreadsheet": "Sheet",
    "application/vnd.google-apps.presentation": "Slide",
    "application/vnd.google-apps.form": "Form",
}

DRIVE_FIELDS = (
    "files(id,name,mimeType,modifiedTime,createdTime,webViewLink,"
    "lastModifyingUser,owners,sharingUser),nextPageToken"
)


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
        r = subprocess.run(full, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"gog error ({' '.join(cmd[:3])}): {r.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except Exception as exc:
        print(f"gog exception ({' '.join(cmd[:3])}): {exc}", file=sys.stderr)
        return None


def fetch_drive_page(query: str, page_token: Optional[str], account: Optional[str]) -> Optional[dict]:
    cmd = [
        "drive", "ls",
        "--all-drives",
        f"--query={query}",
        f"--fields={DRIVE_FIELDS}",
        "--max=100",
    ]
    if page_token:
        cmd.append(f"--page={page_token}")
    return run_gog(cmd, account)


def search_drive(associate_email: str, since: date, until: date, account: Optional[str]) -> dict:
    """
    Find Workspace files (Docs, Sheets, Slides, Forms) that the associate last
    modified, visible to the authenticated user, in the given date range.
    """
    mime_clause = " or ".join(f"mimeType='{m}'" for m in WORKSPACE_MIMES)
    since_ts = f"{since}T00:00:00"
    until_ts = f"{until}T23:59:59"
    query = (
        f"({mime_clause})"
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
            last_modifier = (f.get("lastModifyingUser") or {}).get("emailAddress", "")
            owner_emails = [(o.get("emailAddress", "")) for o in (f.get("owners") or [])]
            if associate_email not in (owner_emails + [last_modifier]):
                continue  # skip files the associate didn't touch
            all_files.append({
                "id": f.get("id"),
                "name": f.get("name"),
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
    Search the authenticated user's Gmail for threads sent by or to the associate.
    Uses Gmail query syntax: after/before accept YYYY/MM/DD.
    """
    since_fmt = since.strftime("%Y/%m/%d")
    until_fmt = until.strftime("%Y/%m/%d")

    sent_by = search_gmail_query(
        f"from:{associate_email} after:{since_fmt} before:{until_fmt}", account
    )
    sent_to = search_gmail_query(
        f"to:{associate_email} after:{since_fmt} before:{until_fmt}", account
    )

    # Deduplicate by thread ID (thread can appear in both queries)
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


def main() -> None:
    p = argparse.ArgumentParser(
        description="Search Google Drive and Gmail for an associate's activity via the gog CLI."
    )
    p.add_argument("--email", required=True, help="Associate's Google / work email address")
    p.add_argument("--since", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--until", required=True, help="End date YYYY-MM-DD")
    p.add_argument("--name", help="Associate display name (metadata only)")
    p.add_argument("--account", help="gog account email or alias to authenticate as")
    p.add_argument("--skip-drive", action="store_true", help="Skip Google Drive search")
    p.add_argument("--skip-gmail", action="store_true", help="Skip Gmail search")
    args = p.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until)

    result: dict = {
        "associate": {"name": args.name, "email": args.email},
        "timeframe": {"since": str(since), "until": str(until)},
    }

    if not args.skip_drive:
        result["drive"] = search_drive(args.email, since, until, args.account)

    if not args.skip_gmail:
        result["gmail"] = search_gmail(args.email, since, until, args.account)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
