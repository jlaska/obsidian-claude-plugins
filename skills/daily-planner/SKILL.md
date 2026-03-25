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

### 4. Generate Meeting Preparation

After the script completes, build a `# Meeting Preparation` section for the daily note.

#### 4a. Parse the daily note and classify meetings by time

Read the daily note created in Step 3. Parse the `# 📅 Meetings` table to extract for each row:
- Meeting wikilink stem and display title (e.g., `2026-03-24 - Victor - James` / `Victor - James`)
- Time (e.g., `8:30 AM`)
- Attendees list

For each meeting, read its `start:` frontmatter field from the meeting file (`MEETINGS/YYYY/MM-Month/<stem>.md`) and compare to the current time:
- **Upcoming** (`start` is in the future) → generate or regenerate its callout
- **Past** (`start` is in the past) → preserve its existing callout exactly; skip all research and writing for it

On the **first run** (no `# Meeting Preparation` section exists yet), generate callouts for **all** meetings regardless of start time.

#### 4b. For each meeting, find previous meetings

Read the meeting file's frontmatter (`MEETINGS/YYYY/MM-Month/<stem>.md`) to get the `attendees:` list.

**Determine meeting type:**
- 1 attendee → **1:1 meeting**
- 2+ attendees → **group meeting**

**Find previous meetings using `obsidian search` (indexed, fast):**

Use a two-tier strategy based on whether the meeting file has a `recurringEventId` frontmatter field.

**Tier 1 — Recurring meetings** (have `recurringEventId`): Use the series base ID for a precise match. Strip the `_R<timestamp>` suffix if present (e.g., `abc123_R20251125T133000` → `abc123`), then query:

```bash
obsidian search query='[recurringEventId:<base-id>]' path="MEETINGS/" limit=5
```

Obsidian tokenizes on underscores, so the base ID matches files that store the full value (with `_R` suffix) and files that store the stripped value — returning all instances of the same calendar series regardless of title variations.

**Tier 2 — Non-recurring meetings** (no `recurringEventId`): Use the `file:` operator to match by filename. This catches naming variations (e.g., `Jonathan Newton and James`, `Jonathan - James`) and is case-insensitive.

*1:1 meetings:* Search for the attendee's first name and "James":
```bash
obsidian search query='file:"<Person FirstName>" file:"James"' path="MEETINGS/" limit=5
```

*Group meetings:* Search for the series title words:
```bash
obsidian search query='file:"<Series Title>"' path="MEETINGS/" limit=5
```

Exclude today's file from all results. Take the last 2-3 by filename (dates sort chronologically).

> **Note:** These commands require Obsidian to be running. They use the indexed search — no filesystem scanning needed.

#### 4c. Read and summarize previous meetings

For each previous meeting file found, read it and extract a 1-sentence TL;DR (~15 words) using this priority:
1. `### Summary` content under `## Notes by Gemini` (preferred)
2. `## Actions` bullet items (fallback)
3. `## Agenda` content (last resort)
4. "No summary available" if none found

Format the link as `[[YYYY-MM-DD - Title\|Month DD, YYYY]]` for clean display.

#### 4d. Gather Parking Lot items (1:1 meetings only)

For each 1:1 meeting, read `PEOPLE/<Person Name>.md` and extract bullet items from the `# Parking Lot` section. Skip if the file doesn't exist or has no Parking Lot section.

#### 4e. Generate suggested topics

Derive 2-3 suggested topics per meeting from:
1. Open action items (`- [ ]` tasks or "will" commitments) from previous meetings
2. Topics needing follow-up from previous summaries
3. Parking Lot items from the PEOPLE file (1:1s only)

If no previous meetings exist: use "Introductions and agenda setting" as the only suggestion.

#### 4f. Write the section to the daily note

Build the full `# Meeting Preparation` section with one foldable callout per meeting.

**Format per meeting:**

```
> [!tip]- [[YYYY-MM-DD - Title\|Display Title]] (HH:MM AM/PM)
> **Previous meetings:**
> - [[YYYY-MM-DD - Title\|Month DD, YYYY]] - One-sentence summary
> - [[YYYY-MM-DD - Title\|Month DD, YYYY]] - One-sentence summary
>
> **Suggested topics:**
> - Follow up on [specific item] from [date]
> - [Parking lot item]
> - [Ongoing topic from previous discussions]
```

`[!tip]-` makes the callout foldable (collapsed by default). Every line inside must be prefixed with a blockquote marker.

**Placement and update rules:**

**First run** (no `# Meeting Preparation` section exists): Generate callouts for all meetings and insert the full section immediately after the `# 📅 Meetings` table, before the next `---` separator.

**Re-run** (section already exists): Use surgical `Edit` operations — do **not** replace the entire section. Instead:
1. For each **upcoming** meeting: find and replace its existing callout (identified by matching the meeting wikilink in the `[!tip]-` header), or append a new callout if it doesn't have one yet.
2. For each **past** meeting: leave its existing callout completely untouched — do not read, modify, or regenerate it.
3. If a meeting has been removed from the calendar (no longer in the meetings table): remove its callout.
4. Preserve the ordering of callouts to match the meetings table (by start time).

Use the `Edit` tool for all writes — never rewrite the entire daily note file.

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
recurringEventId: <google calendar series id>  # only present for recurring events
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
