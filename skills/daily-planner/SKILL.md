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
- Enrich meeting files with Google Meet links, descriptions, document attachments, and linked Notion notes
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

### 3b. Fetch Notion Notes

Many meetings link a Notion page (agenda/notes) in the event description. Step 3 records it as the `notion:` frontmatter key on the meeting file and adds a 📓 link to the daily note's Meetings table. This step pulls the relevant content into a `## Notes from Notion` section, following the same replace-in-place process as `## Notes by Gemini` (refreshed on every run; everything else in the note is untouched). `fetch_notion_section.py` does the fetch (via `ntn`) and all selection, cleaning and writing deterministically. Do not hand-edit, summarise or select from the fetched content.

```bash
python3 "$SKILL_BASE/list_notion_meetings.py" --vault-root "$VAULT_ROOT" > "$CACHE_DIR/notion_meetings.json"
```

Output: JSON array of `{stem, path, start, notion_url, page_id, is_database, has_section}`. If empty, skip this step. Load the Notion tool first: `ToolSearch("select:mcp__claude_ai_Notion__notion-fetch")`.

For each entry (all of today's meetings, past and upcoming):

1. **`is_database: true`** (URL has `?v=`): link only. Do not fetch; the 📓 link already exists.
2. **Primary path: `ntn`.** If `ntn whoami` succeeds, the script fetches the page itself (and the child page for index pages), so there is no raw-file handoff. Pass `--vault-root "$VAULT_ROOT"` so people mentions link to PEOPLE notes:

   ```bash
   python3 "$SKILL_BASE/fetch_notion_section.py" --ntn --meeting-file "<path>" --date "<YYYY-MM-DD of the day being planned>" --vault-root "$VAULT_ROOT"
   ```

3. **Fallback: Notion connector** (only if `ntn` is not installed or not logged in). Load it with `ToolSearch("select:mcp__claude_ai_Notion__notion-fetch")`, call `notion-fetch` with `notion_url` once per `page_id` (several series share one page), and get the raw result into a file. If the result was auto-saved to a file, use that path as-is. Otherwise write the returned JSON text verbatim, with no editing or summarising, using a single quoted heredoc:

   ```bash
   cat > "$CACHE_DIR/notion_<page_id>.txt" <<'NOTION_RAW_EOF'
   <paste the tool result exactly>
   NOTION_RAW_EOF
   ```

   Then run the same script with `--raw-file "<raw file>"` in place of `--ntn`.

The script prints one JSON line: `{status, reason, matched_dates, child_candidate, warnings}`. It selects the section for the date (exact match, else the most recent earlier section within 14 days, with a visible "Notes from ..." line), writes `## Notes from Notion` with a `Source:` line, and never writes unless the status is `written`.

**Act on `status`:**
- `written`: done. If `matched_dates` differs from the meeting date, the section says which earlier date it shows.
- `no_section`: nothing to write. Report "no notes for this date" with the `reason`.
- `needs_child`: only happens in the connector fallback. Fetch the URL in `child_candidate` with `notion-fetch`, save it to `$CACHE_DIR/notion_<child page id>.txt` the same way, and re-run with that raw file (the child's own URL is used for the Source line).
- `error`: report explicitly, naming the meeting and the `reason` (for example not found vs no access). Never report an error as "no notes". For auth failures, suggest `ntn login`. Continue with the remaining meetings.
- Also surface any `warnings` briefly.

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
- `previous_meetings` — list of `{stem, path, gemini_summary, actions_text, notion_text, agenda_text}`
- `parking_lot` — list of raw bullet strings from PEOPLE file
- `is_first_run` — `true` if no `# Meeting Preparation` section exists yet

#### 4b. For each upcoming meeting (or all on first run), generate callouts

Read the `CONTEXT` JSON array and for each meeting with `status: "upcoming"` (or all if `is_first_run`):

1. **Summarize** each previous meeting: extract a 1-sentence TL;DR (~15 words) from the raw `gemini_summary`, `notion_text`, `actions_text`, or `agenda_text` — in that priority order. Use "No summary available" if all are empty.

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

Surface your stale Linear issues. Supabase uses Linear (not Jira); this step uses the Linear MCP tools (`mcp__claude_ai_Linear__*`). They are deferred, so load them first with `ToolSearch("select:mcp__claude_ai_Linear__list_issues")`.

**Scope (for now):** only issues **assigned to you**. Do not include team-wide issues, unassigned issues, or issues you watch/subscribe to (the connector doesn't expose subscribers; see "Future" below).

#### 6a. Gather stale Linear issues

**Stale definition:** status name is exactly `In Progress` and `updatedAt` is 7 or more days before today. Do not include `In Review`, `Needs Release` or `Blocked`, even though Linear groups them under the `started` type.

```
list_issues(assignee="me", state="In Progress", limit=250,
            fields=["title","team","status","updatedAt","url","priority"])
```

Page with `cursor` while `hasNextPage` is true. This is not limited to particular teams. `list_issues` can only filter by "updated after", so compute staleness client-side: `days_stale = today - updatedAt`, keep `days_stale >= 7`. Valid `fields` values include `createdBy` (not `creator`).

#### 6b. AI triage and insert into daily note

Read the daily note at `$DAILY_NOTE` (the absolute path from Step 1).

If there are no stale issues, skip this step entirely (no empty section in the daily note). If the section already exists from an earlier run, remove it.

Generate one callout with an AI-suggested action for each item, sorted by days stale, oldest first. Insert or replace the `# ⚠️ Needs Attention` section in the daily note, positioned **above** `# 📅 Meetings`.

**Callout format:**

```markdown
# ⚠️ Needs Attention

> [!warning]- My Stale In-Progress Issues (N items)
> - **[KUBE-123](https://linear.app/supabase/issue/KUBE-123)** — *Summary* (Team, N days stale)
>   → **Update status or close if complete**
```

The `→ **bold text**` line is an AI-generated suggested action tailored to each item.

**Future (not implemented):** a second callout "Stale issues I'm watching", and optional team-wide groups for COMPUTE (with sub-teams) and NET.

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
| `list_notion_meetings.py` | Meetings linking a Notion page (for Step 3b) | `--vault-root`, `--date` | JSON: meetings with `notion:` URL, page ID, database flag |
| `fetch_notion_section.py` | Fetch via `ntn` (or convert a raw `notion-fetch` result) and insert/replace `## Notes from Notion` | `--meeting-file`, `--date`, `--ntn` or `--raw-file`, `--notion-url`, `--vault-root` | One JSON status line: `written`, `no_section`, `needs_child` or `error` |
| `notion_convert.py` | Notion to Obsidian markdown converter (library, small CLI) | raw `notion-fetch` JSON or its text, `--date` | JSON: `status`, `markdown`, `matched_dates`, `child_candidate`, `warnings` |
| `write_notion_section.py` | Insert/replace `## Notes from Notion` from a ready-made markdown file (manual use) | `--meeting-file`, `--content-file` | Meeting file updated |
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
