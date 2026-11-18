---
name: associate-artifact-search
description: Search Linear, GitHub, Google Drive, and Gmail for an associate's work products (issues, PRs, commits, reviews, comments, documents, email threads) over a configurable time window.
category: Fleet Engineering
---

# Associate Artifact Search

Gather an associate's work products across Linear, GitHub, Google Drive, and Gmail for a given time window. All sources are read-only discovery. Each platform is queried by a dedicated script that outputs structured JSON; the skill then produces a markdown highlight summary.

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
   - **Email**: Read the `mail` field (Red Hat contacts) or `email` field (external contacts)
   - **Linear identity**: Use the same email value (resolved to a Linear user in Step 2 via the Linear MCP `list_users`)
   - **Google email**: Use the same email value

4. Pass any discovered values as defaults to Step 1.

---

## Step 1 — Gather inputs

**Pre-populated defaults**: When Step 0 found a People note with platform identities, add a recommended first option to each relevant question below. For example, if GitHub username `vkareh` was extracted from the People note, Q2 should have `"Use vkareh (from People note) (Recommended)"` as the first option. If a field has no default from Step 0, leave that question unchanged. Original options always remain available for override.

Use `AskUserQuestion` with the following questions across two calls:

**Call 1** — Identity (3 questions):

**Q1** (header: `Linear`): "How should we identify the associate in Linear?"
- "By email address" — user enters the email via the Other field (usually the same as the People note email)
- "By name or Linear user ID" — user enters it via Other
- "Skip Linear" — exclude Linear from this search

**Q2** (header: `GitHub`): "What is the associate's GitHub username?"
- "Enter GitHub username" — user types it via the Other field
- "Skip GitHub" — exclude GitHub from this search

**Q3** (header: `Google`): "What is the associate's Google / work email address for Drive and Gmail search?"
- "Enter email address" — user types it via the Other field (usually the same as the People note email)
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

### Linear user

Linear is resolved through the Linear MCP server first. The search script then uses the resolved user ID.

1. Call `mcp__claude_ai_Linear__list_users` with `query=<associate-email>` (or the name, if no email).
2. If exactly one user matches, pass that user's `id` as `--linear-user`.
3. If several users match, prefer the one whose `email` equals the People note email. Otherwise ask the user to pick.
4. If nothing matches (or the Linear MCP is unavailable), pass the raw email as `--linear-user`; the script tries an email lookup through the GraphQL API. If that also fails, ask the user for a name or Linear user ID. Do not guess.

`search_linear.py` needs `LINEAR_API_KEY` in the environment (personal API key from Linear > Settings > Security & access). If it is unset, tell the user how to create one and continue with the other sources.

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
# Linear (if not skipped; requires LINEAR_API_KEY)
uv run skills/associate-artifact-search/search_linear.py \
  --linear-user <user-id-or-email-or-name> \
  --since <YYYY-MM-DD> --until <YYYY-MM-DD> \
  [--name "<Display Name>"]

# GitHub (if not skipped)
uv run skills/associate-artifact-search/search_gh.py \
  --github-user <username> \
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

### Gmail message-content pass

`search_gog.py` returns thread metadata. For the associate's **sent-by** results, fetch the thread content for substantive candidates so the summary reflects the messages, audience, and work rather than just thread counts or subjects:

```bash
gog --json --readonly --wrap-untrusted gmail thread get <thread-id> \
  --full --sanitize-content
```

- Check individual message `From` and sent date inside each thread. Attribute content only to messages authored by the associate within the requested date range; the thread may include other authors or out-of-range replies.
- Read To/Cc fields to classify reach: one-to-one, small working group, team/org/broad distribution (note broad aliases when visible), or external/customer/partner.
- Review message bodies for substantive work; classify announcements, decisions/opinions, status reports, meeting summaries/follow-ups, coordination/requests, technical/design communications, and automated or calendar traffic. Separate routine/automated traffic from authored contributions.
- Process thread reads sequentially or in small batches to avoid Gmail API rate limits. If rate-limited, pause and retry.

---

## Step 4 — Present results

Parse the JSON output and produce a markdown summary with one section per tool searched:

### Linear
- **Created** — issues the associate opened (identifier, title, team, status)
- **Completed** — issues assigned to them that reached a completed state in the window
- **Status changes** — issues where they moved the workflow state (from → to, date)
- **Updated** — assigned issues touched in the window, grouped by project or cycle
- **Comments** — group by `context` (`issue`, `document`, `project`, `project_update`, `initiative`) and name `context_title`; for issues include the identifier. Give a brief description of the substance (do not paste long bodies)
- **Linked PRs** — each issue's `linked` entries (GitHub attachments). Match PRs to issues using these attachments or the issue identifier in PR titles, not `branch`: Linear generates `branch` from the API key owner's username, so it does not reflect the associate. When a linked PR also appears in the GitHub `authored_prs` results, show them together (identifier → PR) rather than listing them twice. A PR linked from Linear that GitHub did not return is still real work: list it as Linear-linked instead of dropping it.

If the script reports an error (for example a missing `LINEAR_API_KEY`), state that Linear was not searched and why. If there is no activity, say so explicitly.

### GitHub

The script combines `gh search` (covers private and org-internal repos you can read) with the public events feed (which returns nothing for private/internal repos). Prefer the search-based sections; use the events-based ones as a supplement.

- **Authored PRs** (`authored_prs`) — grouped by repo; title, state, and whether it was merged in the window (`merged_in_window`). A PR created before the window but merged inside it is still in scope.
- **Commits** (`commits`, plus `pushes` from events) — grouped by repo; first line of the message. The two lists overlap; `summary.commits` is already de-duplicated by SHA.
- **PR reviews** (`reviewed_prs`) — other people's PRs the associate reviewed in the window: repo, PR title, author, and the review states (APPROVED, CHANGES_REQUESTED, COMMENTED). Call out change requests, since they show substantive review.
- **PR comments** (`commented_prs`) — other people's PRs where the associate left conversation comments in the window.
- **Issues**, **Issue Comments**, **Releases**, **Branches created** — from the events feed (`issues`, `issue_comments`, `releases`, `creates`); typically only public repos.
- If `warnings` is non-empty (for example the events feed or a search failed, or results were truncated), mention it. Do not present an empty GitHub section as "no activity" when a source failed.

### Google Drive + Gmail (if included)
- **Drive** — Docs, Sheets, Slides, Forms the associate owns or last modified (name, type, modified date, link)
- **Gmail** — lead with a content-level synthesis of the associate's authored work messages: 3–5 themes describing where their attention went, how they showed up (e.g. decisions, opinions, facilitation, status reporting, follow-through), the scope/audience, and the apparent impact. Support this with 3–5 representative dated subject examples and a brief description of message type, audience category, and contribution. Identify broad distribution aliases when present. Summarize recurring/automated status mail and calendar traffic separately, rather than treating volume as impact. Thread counts for sent-by/sent-to can be included as context, but are not a substitute for the content analysis.
- Use the authenticated mailbox's sent-by search for attribution. Incoming threads may be used as context, but do not describe them as the associate's contributions unless an authored message is present.
- Summarize personnel topics only at a high level; omit personal, health, HR-case, and other sensitive details. Do not reproduce private message contents verbatim.
- If message bodies are unavailable or the search is rate-limited, state that limitation and avoid inferring impact from subject lines alone.

### Summary
End with a 3–5 sentence paragraph naming the associate and calling out:
- Where their effort was concentrated (which platform, which repos/projects/Linear teams)
- Notable deliverables (releases, merged PRs, closed issue clusters, Drive documents)
- Any gaps (e.g. no Linear activity, a source that failed or was skipped)

---

## Supporting files

- `skills/associate-artifact-search/search_linear.py` — Linear GraphQL API (read-only); needs `LINEAR_API_KEY`. Finds created, completed, updated, status-transition, and comment activity by user
- `skills/associate-artifact-search/search_gh.py` — GitHub PRs, reviews, comments and commits via `gh search` (private/internal repos included), supplemented by public events via `gh api`
- `skills/associate-artifact-search/search_gog.py` — Google Drive (Docs/Sheets/Slides/Forms) and Gmail via the `gog` CLI
