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

## Workflow

### 1. Discover Vault Configuration

Read Obsidian configuration to determine:
- Vault root path (from `~/Library/Application Support/obsidian/obsidian.json`)
- Daily notes folder and date format (from `.obsidian/daily-notes.json`)
- Meetings folder path (from `CLAUDE.md`)
- Templates location (from `.obsidian/templates.json`)

### 2. Fetch Calendar Data

```bash
gog calendar events --today --json --all-pages > /tmp/calendar_events.json
```

### 3. Run the Processing Script

> **CRITICAL: Run the Python script below. Do NOT manually create files or interpret the "Script Reference" section as steps to perform yourself — that section documents what the script does internally.**

```bash
python3 <skill_base_dir>/process_calendar.py "<vault_root>" /tmp/calendar_events.json
```

Replace `<skill_base_dir>` with the base directory shown at the top of this skill's context, and `<vault_root>` with the vault path discovered in Step 1.

The script fully handles filtering, attendee matching, file creation/updating, Gemini transcript fetching, and daily note generation. **Check its stdout for any errors or warnings.**

---

## Script Reference

> This section documents what `process_calendar.py` does internally. It is for reference only — the script performs all these steps automatically when invoked in Step 3.

### Filtering Logic

**Skip automatically:**
- `eventType: "workingLocation"` — office/location tracking events
- `responseStatus: "declined"` — meetings you declined
- `responseStatus: "tentative"` or `"needsAction"` — not yet accepted
- No `attendees` field, or only yourself as attendee
- `guestsCanSeeOtherGuests: false` AND `guestsCanInviteOthers: false` — broadcast events

**Create meeting notes for:**
- Accepted meetings with at least one other attendee

### File Paths

**Daily note**: `DAILY_NOTES/YYYY/MM-Month/YYYY-MM-DD DayOfWeek.md`
**Meeting files**: `MEETINGS/YYYY/MM-Month/YYYY-MM-DD - Title.md`

Format derived from `.obsidian/daily-notes.json` format field (`YYYY/MM-MMMM/YYYY-MM-DD dddd`).

### Attendee Matching

For each attendee email:
1. Search `PEOPLE/` files for `mail: <email>` in frontmatter
2. Match by filename (case-insensitive)
3. Fall back to `gog people search <email> --json`
4. Use calendar display name as last resort

Output: `"[[Person Name]]"` (quoted wikilink)

### Meeting File Frontmatter

```yaml
---
attendees:
  - "[[Person Name]]"
tags:
  - Meetings
created: YYYY-MM-DD HH:MM
start: YYYY-MM-DDTHH:MM:SS-TZ
end: YYYY-MM-DDTHH:MM:SS-TZ
gmeet: <hangout_link>
agenda: <google doc URL>
gemini: <gemini transcript URL>
URL: <calendar event link>
---
```

### Gemini Transcript Fetching

When a meeting has a `gemini:` URL, the script:
1. Extracts the Google Doc ID from the URL
2. Runs `gog docs cat --raw --results-only <doc_id>` to fetch the raw Docs API JSON
3. Parses it into markdown (preserving bold, headings)
4. Extracts Summary, Details, and Suggested next steps sections
5. Inserts a populated `## Notes by Gemini` section into the meeting file

This runs for all meetings (new and existing) on every invocation.

### Daily Note Meetings Table

```markdown
# 📅 Meetings

| Time | Meeting | Attendees | Summary |
|------|---------|-----------|---------|
| 8:00 AM | [[YYYY-MM-DD - Title\|Title]] | [[Person1]], [[Person2]] | [🤖](https://...) |
| 8:30 AM | [[YYYY-MM-DD - Title 2\|Title 2]] | [[Person3]] |  |
```

Attendees truncated after 6 with `...`. Summary column shows 🤖 linked to Gemini doc when available.

### Attachment Classification

| Title contains | Property |
|----------------|----------|
| "gemini" | `gemini` |
| recording mime type (video/mp4) | `recording` |
| "minutes", "summary", "recap" | `minutes` |
| Google Slides mime type | `slides` |
| everything else (Google Docs) | `agenda` |

### Idempotency

On re-runs, the script:
- Creates new meeting files only for newly-detected events
- Adds missing frontmatter properties (never overwrites existing values)
- Updates `## Notes by Gemini` content if it changed
- Merges new meetings into the daily note's `# 📅 Meetings` table

**Never overwrites:** user content in `## Agenda`, `## Actions`, or any manually-edited frontmatter fields.

## Directory Structure

```
DAILY_NOTES/YYYY/MM-Month/YYYY-MM-DD DayOfWeek.md
MEETINGS/YYYY/MM-Month/YYYY-MM-DD - Title.md
PEOPLE/First Last.md
TEMPLATES/
```
