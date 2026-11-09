---
name: daily-planner
description: Create daily agenda from Google Calendar - generates daily note and meeting files with enriched calendar data (gmeet links, descriptions, attachments)
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - Edit
---

# Daily Planner

Automates daily planning by fetching Google Calendar events and creating/updating Obsidian notes.

## When to Use

Invoke `/daily-planner` at the start of your day to:
- Create today's daily note (if it doesn't exist)
- Create meeting note files for actual meetings (auto-filtered)
- Enrich meeting files with Google Meet links, descriptions, and document attachments
- Match calendar attendees to People notes
- Link all meetings from the daily note
- Generate a Meeting Preparation section with previous meeting summaries and suggested topics

## Workflow

### 1. Discover Vault and User Identity

Run the discovery scripts to get vault configuration and user identity. All subsequent steps use these values.

```bash
SKILL_BASE="<skill_base_dir>"
CACHE_DIR="$HOME/.cache/obsidian-claude-plugins"
mkdir -p "$CACHE_DIR"

VAULT_CONFIG=$("$SKILL_BASE/discover_vault.py" 2>/dev/null || python3 "$SKILL_BASE/discover_vault.py")
VAULT_ROOT=$(echo "$VAULT_CONFIG" | python3 -c "import json,sys; print(json.load(sys.stdin)['vault_root'])")
DAILY_NOTE=$(echo "$VAULT_CONFIG" | python3 -c "import json,sys; print(json.load(sys.stdin)['today']['daily_note_absolute'])")

SELF_JSON=$("$SKILL_BASE/discover_self.py" 2>/dev/null || python3 "$SKILL_BASE/discover_self.py")
echo "$SELF_JSON" > "$CACHE_DIR/self.json"
```

Replace `<skill_base_dir>` with the base directory shown at the top of this skill's context.

### 2. Fetch Calendar Events

Discover all `gog`-authenticated Google accounts, fetch today's events from each, merge and filter them.

```bash
python3 "$SKILL_BASE/discover_events.py" \
  --self-json "$CACHE_DIR/self.json" \
  --cache-dir "$CACHE_DIR" \
  > "$CACHE_DIR/events.json"
```

By default only the **primary calendar** of each account is queried (not shared or coworker calendars). To include additional calendars, use `--calendars "cal-id-1,cal-id-2"`. To fetch all visible calendars (old behavior), use `--all-calendars`.

Check for warnings in stderr. If a `gog` account has an expired token, the script prints a re-auth command and continues with remaining accounts.

### 3. Sync to Vault

Create or update meeting files and the daily note's `# Meetings` table from the fetched events.

```bash
python3 "$SKILL_BASE/sync_to_vault.py" \
  --vault-root "$VAULT_ROOT" \
  --events-json "$CACHE_DIR/events.json" \
  --self-json "$CACHE_DIR/self.json"
```

**Check stdout** for any errors, warnings about cancelled meetings, or skipped events.

### 4. Generate Meeting Preparation

#### 4a. Gather meeting context

Run the context-gathering script to collect previous meetings, actions, and parking lot items. This script does all the mechanical vault reading so you don't have to.

```bash
CONTEXT=$(python3 "$SKILL_BASE/gather_meeting_context.py" \
  --vault-root "$VAULT_ROOT" \
  --self-json "$CACHE_DIR/self.json")
```

The output is a flat JSON array. Each element has this shape:
- `stem`, `display_title`, `time`, `start_iso` — meeting identity
- `status` — `upcoming` or `past` (compare to current time)
- `type` — `one_on_one` or `group`
- `attendees` — wikilinks like `[[Person Name]]`
- `previous_meetings` — list of `{stem, path, gemini_summary, actions_text, agenda_text}`
- `parking_lot` — list of raw bullet strings from PEOPLE file
- `is_first_run` — `true` if no `# Meeting Preparation` section exists yet

#### 4b. For each upcoming meeting (or all on first run), generate callouts

Read the `CONTEXT` JSON array and for each meeting with `status: "upcoming"` (or all if `is_first_run`):

1. **Summarize** each previous meeting: extract a 1-sentence TL;DR (~15 words) from the raw `gemini_summary`, `actions_text`, or `agenda_text` — in that priority order. Use "No summary available" if all are empty.

2. **Generate suggested topics** by reasoning across:
   - Open action items (`- [ ]` items in `actions_text`) → follow-up topics
   - `parking_lot` items from PEOPLE file → include ALL of them, tag each with "(Parking Lot)"
   - Themes from previous meeting summaries → ongoing topics needing attention

3. **Write the callout** to the daily note using this format:

```
> [!tip]- [[YYYY-MM-DD - Title\|Display Title]] (HH:MM AM/PM)
> **Previous meetings:**
> - [[YYYY-MM-DD - Title\|Month DD, YYYY]] - One-sentence summary
>
> **Suggested topics:**
> - Follow up on [specific item] from [date]
> - [Topic from parking lot item] (Parking Lot)
```

**Placement rules:**
- **First run** (any meeting has `is_first_run: true`): Write all callouts in time order and insert the full `# Meeting Preparation` section immediately after the `# 📅 Meetings` table.
- **Re-run**: Use surgical `Edit` operations targeting callouts that are `upcoming` OR have `needs_content: true` (placeholder skeletons inserted by sync_to_vault.py for newly added meetings). Leave `past` meeting callouts with real content completely untouched. Note: removed meeting callouts are already cleaned up automatically by `sync_to_vault.py` in Step 3 — you do not need to handle deletions.

### 5. Cancelled Meeting File Cleanup

After Step 3, check stdout for lines beginning with `⚠️  Cancelled meeting files to review:`. For each listed file, prompt the user before taking any action:

- **"no user modifications"** → ask: *"[Meeting title] was cancelled. The meeting file has no notes — delete it?"* → delete on confirmation
- **"has user content in ## Actions"** → ask: *"[Meeting title] was cancelled but has notes. Keep or delete?"* → act on user's response

**Never delete a meeting file without explicit user confirmation.**

### 6. Needs Attention

Surface stale Linear issues in the tracked teams. Supabase uses Linear (not Jira); this step uses the Linear MCP tools (`mcp__claude_ai_Linear__*`). They are deferred, so load them first with `ToolSearch("select:mcp__claude_ai_Linear__list_issues")`.

#### 6a. Gather stale Linear issues

**Tracked teams** (edit this list to change scope):
- `COMPUTE` with `includeSubTeams: true` (covers Capacity, Branching, Compute - General, Compute - Kubernetes, SupaSi, Multigres Integration)
- `NET` (Networking)

**Stale definition:** status name is exactly `In Progress` and `updatedAt` is 7 or more days before today. Do not include `In Review`, `Needs Release` or `Blocked`, even though Linear groups them under the `started` type.

Run one call per tracked team:

```
list_issues(team="COMPUTE", includeSubTeams=true, state="In Progress", limit=250,
            fields=["title","team","status","updatedAt","assignee","url","priority"])
list_issues(team="NET", state="In Progress", limit=250,
            fields=["title","team","status","updatedAt","assignee","url","priority"])
```

Page with `cursor` while `hasNextPage` is true. `list_issues` can only filter by "updated after", so compute staleness client-side: `days_stale = today - updatedAt`, keep `days_stale >= 7`. Valid `fields` values include `createdBy` (not `creator`).

Split the stale issues into:
1. **Assigned to you**: assignee is you (`whoami` / `self.json` identity; Linear display name `jameslaska`, name "James Laska").
2. **Unassigned**: assignee is null.
3. **Rest of the tracked teams**: everything else, grouped by assignee.

#### 6b. AI triage and insert into daily note

Read the daily note at `$DAILY_NOTE` (the absolute path from Step 1).

If there are no stale issues, skip this step entirely (no empty section in the daily note).

For each non-empty group, generate a callout block with an AI-suggested action for each item. Sort items by days stale, oldest first. Group 3 can be long, so list it by assignee and keep it collapsed. Insert or replace the `# ⚠️ Needs Attention` section in the daily note, positioned **above** `# 📅 Meetings`.

**Callout format:**

```markdown
# ⚠️ Needs Attention

> [!warning]- Stale In-Progress Issues Assigned to You (N items)
> - **[KUBE-123](https://linear.app/supabase/issue/KUBE-123)** — *Summary* (Team, N days stale)
>   → **Update status or close if complete**

> [!todo]- Unassigned In-Progress Issues (N items)
> - **[SUP-38](https://linear.app/supabase/issue/SUP-38)** — *Summary* (Team, N days stale)
>   → **Find an owner or move back to Backlog**

> [!info]- Stale In-Progress Issues in COMPUTE / NET (N items)
> **Assignee Name**
> - **[GENCOMP-213](https://linear.app/supabase/issue/GENCOMP-213)** — *Summary* (Team, N days stale)
>   → **Ask for status or ask whether it is still in progress**
```

The `→ **bold text**` line is an AI-generated suggested action tailored to each item.

**Section management:**

Read `$DAILY_NOTE` and check whether `# ⚠️ Needs Attention` already exists.

- **Section missing** (common — daily notes created before template update won't have it): Use the `Edit` tool on `$DAILY_NOTE` to insert the full section — heading, callout blocks, and a blank line — immediately **before** the `# 📅 Meetings` line. Set `old_string` to `# 📅 Meetings` and `new_string` to the new section text followed by `# 📅 Meetings`.
- **Section exists**: Use the `Edit` tool on `$DAILY_NOTE` to replace everything from `# ⚠️ Needs Attention` up to (but not including) the next `#` heading with the regenerated section. No user content to preserve — fully regenerated each run.

---

## Script Reference

| Script | Purpose | Input | Output |
|--------|---------|-------|--------|
| `discover_vault.py` | Vault config | None (auto-discovers) | JSON: vault_root, folder paths, date format, today's paths |
| `discover_self.py` | User identity | None (auto-discovers) | JSON: username, emails, display_name, first_name |
| `discover_events.py` | Calendar fetch + filter | `--self-json`, `--cache-dir`, `--calendars`, `--all-calendars` | JSON: filtered events array |
| `sync_to_vault.py` | Meeting files + daily note | `--vault-root`, `--events-json`, `--self-json` | Vault files updated; stdout: status + warnings |
| `gather_meeting_context.py` | Vault introspection for AI prep | `--vault-root`, `--self-json` | JSON: flat array of meetings, each with `is_first_run` |
| `discover_jira_actions.py` | **Unused** (Supabase uses Linear; Step 6 uses the Linear MCP tools) | `--self-json`, `--projects`, `--stale-days` | JSON: stale assigned + stale created issues |
| `vault_utils.py` | Shared utilities (library) | — imported by other scripts | — |

All scripts support `--help` for usage details.

## Directory Structure

```
DAILY_NOTES/YYYY/MM-Month/YYYY-MM-DD DayOfWeek.md
MEETINGS/YYYY/MM-Month/YYYY-MM-DD - Title.md
PEOPLE/First Last.md
TEMPLATES/
TRANSCRIPTS/
```
