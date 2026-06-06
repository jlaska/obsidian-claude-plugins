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

The JSON output has this shape per meeting:
- `stem`, `display_title`, `time`, `start_iso` — meeting identity
- `status` — `upcoming` or `past` (compare to current time)
- `type` — `one_on_one` or `group`
- `attendees` — wikilinks like `[[Person Name]]`
- `previous_meetings` — list of `{stem, path, gemini_summary, actions_text, agenda_text}`
- `parking_lot` — list of raw bullet strings from PEOPLE file
- `is_first_run` — `true` if no `# Meeting Preparation` section exists yet

#### 4b. For each upcoming meeting (or all on first run), generate callouts

Read the `CONTEXT` JSON and for each meeting with `status: "upcoming"` (or all if `is_first_run`):

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
- **First run** (`is_first_run: true`): Write all callouts in time order and insert the full `# Meeting Preparation` section immediately after the `# 📅 Meetings` table.
- **Re-run**: Use surgical `Edit` operations — find and replace only `upcoming` meeting callouts (identified by the `[!tip]-` header wikilink). Leave `past` meeting callouts completely untouched.

### 5. Cancelled Meeting File Cleanup

After Step 3, check stdout for lines beginning with `⚠️  Cancelled meeting files to review:`. For each listed file, prompt the user before taking any action:

- **"no user modifications"** → ask: *"[Meeting title] was cancelled. The meeting file has no notes — delete it?"* → delete on confirmation
- **"has user content in ## Actions"** → ask: *"[Meeting title] was cancelled but has notes. Keep or delete?"* → act on user's response

**Never delete a meeting file without explicit user confirmation.**

---

## Script Reference

| Script | Purpose | Input | Output |
|--------|---------|-------|--------|
| `discover_vault.py` | Vault config | None (auto-discovers) | JSON: vault_root, folder paths, date format, today's paths |
| `discover_self.py` | User identity | None (auto-discovers) | JSON: username, emails, display_name, first_name |
| `discover_events.py` | Calendar fetch + filter | `--self-json`, `--cache-dir` | JSON: filtered events array |
| `sync_to_vault.py` | Meeting files + daily note | `--vault-root`, `--events-json`, `--self-json` | Vault files updated; stdout: status + warnings |
| `gather_meeting_context.py` | Vault introspection for AI prep | `--vault-root`, `--self-json` | JSON: meetings array with context |
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
