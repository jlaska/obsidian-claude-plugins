---
name: associate-artifact-search
description: Search Jira, GitHub, GitLab, Google Drive, and Gmail for an associate's work products (issues, PRs, commits, reviews, comments, documents, email threads) over a configurable time window.
category: Fleet Engineering
---

# Associate Artifact Search

Gather an associate's work products across Jira, GitHub, GitLab, Google Drive, and Gmail for a given time window. Each platform is queried by a dedicated script that outputs structured JSON; the skill then produces a markdown highlight summary.

---

## Step 0 — Look up People note defaults

If the user provided a person name, attempt to look up their People note for pre-populated defaults.

1. Resolve the Obsidian vault root:
   ```bash
   cat ~/Library/Application\ Support/obsidian/obsidian.json
   ```
   Parse JSON to find the vault with `"open": true` or highest `ts`.

2. Read `<vault_root>/PEOPLE/<person name>.md`. If the file doesn't exist, skip to Step 1 (fully manual entry).

3. Parse YAML frontmatter and extract:
   - **GitHub username**: From the `social` list, find a URL matching `https://github.com/<user>` and extract `<user>`
   - **GitLab username**: From the `social` list, find a URL matching `https://gitlab.com/<user>` or `https://gitlab.cee.redhat.com/<user>` and extract `<user>`. If no GitLab URL is in the `social` list but the email is `@redhat.com`, derive the GitLab username from the email prefix (e.g., `jbalunas@redhat.com` → `jbalunas`, defaulting to `https://gitlab.cee.redhat.com/jbalunas`)
   - **Email**: Read the `mail` field (Red Hat contacts) or `email` field (external contacts)
   - **Jira identity**: Use the same email value (search_jira.py resolves email → account ID)
   - **Google email**: Use the same email value

4. Pass any discovered values as defaults to Step 1.

---

## Step 1 — Gather inputs

**Pre-populated defaults**: When Step 0 found a People note with platform identities, add a recommended first option to each relevant question below. For example, if GitHub username `vkareh` was extracted from the People note, Q2 should have `"Use vkareh (from People note) (Recommended)"` as the first option. If a field has no default from Step 0, leave that question unchanged. Original options always remain available for override.

Use `AskUserQuestion` with the following questions across two calls:

**Call 1** — Identity (4 questions):

**Q1** (header: `Jira identity`): "How should we identify the associate in Jira?"
- "By email address" — user enters the email via the Other field
- "By Jira account ID" — user enters the ID (format: `accountType:uuid`) via Other
- "From a Jira issue URL" — user provides a URL via Other; agent extracts reporter/assignee account ID
- "Skip Jira" — exclude Jira from this search

**Q2** (header: `GitHub`): "What is the associate's GitHub username?"
- "Enter GitHub username" — user types it via the Other field
- "Skip GitHub" — exclude GitHub from this search

**Q3** (header: `GitLab`): "What is the associate's GitLab username?"
- "Enter GitLab username" — user types it via the Other field
- "Skip GitLab" — exclude GitLab from this search

**Q4** (header: `Google`): "What is the associate's Google / work email address for Drive and Gmail search?"
- "Enter email address" — user types it via the Other field (often the same as their Jira email)
- "Skip Google" — exclude Drive and Gmail from this search

**Call 2** — Time window (1 question):

**Q1** (header: `Timeframe`): "What time period should the search cover?"
- "Last 7 days (Recommended)" — rolling 7-day window ending today
- "Today" — just today
- "This week" — Monday of the current week through today
- "This month" — 1st of the current month through today

For a custom date range or "this quarter", the user can select Other and enter a range as `YYYY-MM-DD:YYYY-MM-DD`.

---

## Step 2 — Resolve identifiers

### Jira account ID

If the user chose "By email address", resolve to an account ID using the Jira REST API:

```bash
JIRA_EMAIL=$(grep 'email:' ~/.config/acli/jira_config.yaml | head -1 | awk '{print $2}')
RAW=$(security find-generic-password -s "acli" -w 2>/dev/null)
JIRA_TOKEN=$(echo "$RAW" | sed 's/go-keyring-base64://' | \
  python3 -c "import sys,base64; d=sys.stdin.read().strip(); print(base64.b64decode(d + '==').decode())")

curl -s -u "$JIRA_EMAIL:$JIRA_TOKEN" \
  "https://redhat.atlassian.net/rest/api/3/user/search?query=<associate-email>&maxResults=1" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['accountId'] if d else 'not found')"
```

If the user chose "From a Jira issue URL", view the issue with `--fields reporter` via `acli jira workitem view <KEY> --fields "reporter" --json` and extract `fields.reporter.accountId`.

### Timeframe → dates

| Selection | --since | --until |
|-----------|---------|---------|
| Last 7 days | 7 days ago | today |
| Today | today | today |
| This week | Monday of this week | today |
| This month | 1st of this month | today |
| Custom (`since:until`) | since | until |

---

## Step 3 — Run the search scripts

Run each selected tool's script independently. All scripts write JSON to stdout.

```bash
# Jira (if not skipped)
uv run skills/associate-artifact-search/search_jira.py \
  --jira-id <account-id-or-email> \
  --since <YYYY-MM-DD> --until <YYYY-MM-DD> \
  [--name "<Display Name>"]

# GitHub (if not skipped)
uv run skills/associate-artifact-search/search_gh.py \
  --github-user <username> \
  --since <YYYY-MM-DD> --until <YYYY-MM-DD> \
  [--name "<Display Name>"]

# GitLab (if not skipped)
uv run skills/associate-artifact-search/search_glab.py \
  --gitlab-user <username> \
  --since <YYYY-MM-DD> --until <YYYY-MM-DD> \
  [--name "<Display Name>"]

# Google Drive + Gmail (if not skipped)
uv run skills/associate-artifact-search/search_gog.py \
  --email <associate@domain.com> \
  --since <YYYY-MM-DD> --until <YYYY-MM-DD> \
  [--account <your-gog-account>] \
  [--name "<Display Name>"]
```

Capture each script's JSON output separately for Step 4.

---

## Step 4 — Present results

Parse the JSON output and produce a markdown summary with one section per tool searched:

### Jira
- **Created** — issues opened by the associate this period (key, type, summary, status)
- **Closed** — issues they transitioned to Done/Closed
- **Edited** — issues where their changelog shows field changes (show which fields)

If no Jira activity is found, note that explicitly rather than omitting the section.

### GitHub
- **Commits / Pushes** — grouped by repo; list branch and commit messages
- **Pull Requests** — opened and merged, with titles and repos
- **PR Reviews** — approvals, change requests, comments (repo + PR title)
- **Issues** — closed/opened with titles
- **Issue Comments** — repo and issue title
- **Releases** — tag, name, repo

### GitLab
- Events grouped by action (approved, commented on, pushed, etc.)
- If no activity found in the period, state the date of their most recent event

### Google Drive + Gmail (if included)
- **Drive** — Docs, Sheets, Slides, Forms the associate owns or last modified (name, type, modified date, link)
- **Gmail** — threads sent by the associate in your mailbox; threads you sent to the associate

### Summary
End with a 3–5 sentence paragraph naming the associate and calling out:
- Where their effort was concentrated (which platform, which repos/projects)
- Notable deliverables (releases, merged PRs, closed issue clusters, Drive documents)
- Any gaps (e.g. no Jira activity, GitLab inactive since date X)

---

## Supporting files

- `skills/associate-artifact-search/search_jira.py` — Jira REST API with changelog expansion; reads credentials from acli config + macOS keychain
- `skills/associate-artifact-search/search_gh.py` — GitHub public events via `gh api --paginate`
- `skills/associate-artifact-search/search_glab.py` — GitLab user events via `glab api`
- `skills/associate-artifact-search/search_gog.py` — Google Drive (Docs/Sheets/Slides/Forms) and Gmail via the `gog` CLI
