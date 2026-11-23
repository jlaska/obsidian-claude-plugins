---
name: daily-planner
description: Create daily agenda from Google Calendar - generates daily note and meeting files with enriched calendar data (gmeet links, descriptions, attachments), plus open incident.io incidents needing your action
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
- List open incident.io incidents where you're Owner or Incident Commander and need to act

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

### 7. Incidents

Surface open incident.io incidents that need your action, in a `# 🚨 Incidents` section. This step uses the incident.io MCP connector (`mcp__claude_ai_incident_io__*`), which acts as you, so no API key is involved.

Read the settings first: `python3 "$SKILL_BASE/skill_config.py"` prints the effective config. Use `incidents.enabled`, `incidents.roles` and `incidents.stale_update_hours`. If `enabled` is false, skip this step entirely and leave the daily note alone.

Get the current UTC time with `date -u +%Y-%m-%dT%H:%M:%SZ` for the staleness check.

**The outcome of this step is exactly one of three things. Never confuse them:**

| Outcome | When | Daily note | Run report |
|---|---|---|---|
| `actions` | Every required call succeeded and at least one item qualifies | Write the section | "incident.io: N incidents need your action" |
| `none` | Every required call succeeded and nothing qualifies | Remove the section | "incident.io: no incidents need your action (checked N open incidents)" |
| `error` | Tools missing, auth failed, or a list call failed | Write the section with a failure callout | "incident.io check FAILED: {reason}" plus the fix |

Report `none` only when you actually got answers back from `identity_show`, `incident_list`, `follow_up_list` and `action_list`. An empty list is `none`; an error, a refusal or a missing tool is `error`.

#### 7a. Load the tools and identify yourself

Load the deferred tools:

```
ToolSearch("select:mcp__claude_ai_incident_io__identity_show,mcp__claude_ai_incident_io__incident_list,mcp__claude_ai_incident_io__incident_show,mcp__claude_ai_incident_io__postmortem_list,mcp__claude_ai_incident_io__follow_up_list,mcp__claude_ai_incident_io__action_list")
```

If the tools are not found, the outcome is `error` with reason "incident.io connector not connected". Fix: connect incident.io in claude.ai connector settings (or `/mcp`).

Call `identity_show`. Keep the user ID: `follow_up_list` and `action_list` need it, because they don't accept "me". If the call fails, or `access_level` is `none`, the outcome is `error` (reason: the error text, or "connection has no access"). Fix: reconnect the incident.io connector.

#### 7b. Gather candidates

1. **My incidents:**

   ```
   incident_list(assigned_to=["me"], status_category=["triage","active","paused","post-incident"],
                 include=["roles","summary"], page_size=50)
   ```

   Page with `after` = `next_cursor` until `has_more` is false. Keep an incident only if one of its `roles` has your user ID and a role name matching `incidents.roles` (case-insensitive). `assigned_to` also matches plain participants, so this filter is required. Record your role(s), the Incident Commander's name (if not you), severity, status name and category, permalink and `chat_channel_url`.

2. **Per incident, by status category:**
   - **triage, active or paused:** call `incident_show(id=<reference>)`. Among its `updates`, find the newest one whose `author.type` is `user` (skip `integration` authors such as `incident_lifecycle`). If none of the returned updates is user-authored, call again with `include=["updates"]`. If there is no human update at all, measure from `reported_at`. If the age is at least `stale_update_hours`, the item is **Stale status update** (show the age in hours and the last author). Otherwise it is **Drive to resolution**: give a one-line, specific next step taken from the incident summary or the latest update.
   - **post-incident:** call `postmortem_list(incident_id=<reference>)`.
     - No documents, or only `state: failed` → **Create postmortem**.
     - Main document (`is_main`, else the first) with `status` not `Completed` → **Finish postmortem** (or **Review postmortem** when the status is `In review`). Link the document `url`.
     - External document without a status → **Check postmortem status** with its link.
     - Postmortem `Completed` → no postmortem item. The incident still appears if it has follow-ups or actions for you; otherwise **Close out the incident** (it is still in post-incident with your role).

3. **My follow-ups and actions** (on any incident, including closed ones and ones where you hold no role):

   ```
   follow_up_list(assignee_id=<my user id>, status=["outstanding"], page_size=50)
   action_list(assignee_id=<my user id>, status=["outstanding"], page_size=50)
   ```

   Page both until `has_more` is false. Attach each item to its incident if that incident is in the list from step 1. Put the rest under **Other incidents**, by incident reference.

**Partial failures:** if `incident_list`, `follow_up_list` or `action_list` fails, the outcome is `error`. If only a per-incident call (`incident_show` or `postmortem_list`) fails, still list that incident, with the line `→ ⚠️ Could not check {updates|postmortem}: {reason}`, and mention it in the run report.

#### 7c. Write the section

Read `$DAILY_NOTE`. Order the incidents: triage, active and paused first, then post-incident. Within each group put the most severe first (Critical, then Major, Minor, Informative), then the most recently updated. **Other incidents** comes last.

Use one callout per incident: `[!danger]` for triage, active or paused, and `[!warning]` for post-incident. Leave them expanded (no `-`).

```markdown
# 🚨 Incidents

> [!danger] [INC-992](https://app.incident.io/…/incidents/992) ~80 unhealthy projects on use1 v3
> Major · Monitoring · **Owner** (IC: Artyom Borissov) · [Slack](<chat_channel_url>)
> - Last status update 27h ago (Jess Fink) → **Post a status update**
> - Follow-up: Add disk-read latency alert (High) → **Complete or reassign**

> [!warning] [INC-978](https://app.incident.io/…/incidents/978) database queue depth at 25,000
> Minor · Reviewing · **Owner** (IC: Rob Shields III)
> - Postmortem "In progress" → **[Finish postmortem](<postmortem url>)**

> [!note] Other incidents
> - [INC-943](<permalink>) Follow-up: Update runbook for EC2 status-check failures → **Complete or reassign**

---

```

For an `error` outcome, write only this in the section:

```markdown
# 🚨 Incidents

> [!failure] incident.io check failed
> <reason>. Incidents needing your action are unknown, not zero.
> Fix: <reconnect the incident.io connector | retry later>

---

```

**Section management** (the section always ends with a `---` line):

- **Outcome `none`**: if `# 🚨 Incidents` exists, use `Edit` to remove everything from `# 🚨 Incidents` up to (but not including) the next `#` heading. Otherwise leave the note alone.
- **Outcome `actions` or `error`, section missing**: use `Edit` to insert the section immediately **before** the `# 📅 Meetings` line (`old_string` `# 📅 Meetings`, `new_string` the section followed by `# 📅 Meetings`). This places it after Needs Attention when that exists.
- **Outcome `actions` or `error`, section exists**: use `Edit` to replace everything from `# 🚨 Incidents` up to (but not including) the next `#` heading. There is no user content to preserve, because the section is regenerated on every run.

---

## Configuration

Company- and person-specific settings live in a user config file, not in the skill:
`~/.config/obsidian-productivity/daily-planner/config.yaml` (honours `XDG_CONFIG_HOME`; override with the `DAILY_PLANNER_CONFIG` environment variable). A missing file means defaults. A broken file or regex is reported as a warning and ignored. See `config.example.yaml` in the skill directory. Run `python3 "$SKILL_BASE/skill_config.py"` to print the effective config.

Supported keys:
- `attendees.ignore_emails`: exact addresses (case-insensitive) never hydrated as meeting attendees, such as group mailing lists.
- `attendees.ignore_patterns`: case-insensitive regular expressions matched against the attendee address, such as Google calendar group and resource addresses.
- `incidents.enabled` (default `true`): run Step 7 and maintain the `# 🚨 Incidents` section.
- `incidents.roles` (default `[Owner, Incident Commander]`): incident role names (case-insensitive) that make an incident yours.
- `incidents.stale_update_hours` (default `24`): an active incident with no human status update for this many hours is flagged **Stale status update**.

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
| `skill_config.py` | User config loader (library): attendee ignore lists, incident settings | `~/.config/obsidian-productivity/daily-planner/config.yaml` | Config dict; run directly to print the effective config |
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
