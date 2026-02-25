---
name: daily-planner
description: Create daily agenda from Google Calendar - generates daily note and meeting files with enriched calendar data (gmeet links, descriptions, attachments)
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

# Daily Planner

Automates daily planning by fetching Google Calendar events and creating/updating Obsidian notes.

## When to Use

Invoke `/daily-planner` at the start of your day to:
- Create today's daily note (if it doesn't exist)
- Interactively select which meetings warrant full documentation
- Create meeting note files for selected calendar events
- Enrich meeting files with Google Meet links, descriptions, and document attachments
- Link meetings and artifacts from the daily note

## Workflow

### 1. Discover Vault Configuration

Use the `obsidian-vault-discovery` skill to determine:
- Vault root path
- Daily notes folder and date format
- Meetings folder path
- Templates location

### 2. Fetch Calendar Data

```bash
gog calendar events --today --json
```

### 3. Filter and Select Meetings

Not all calendar events warrant full meeting documents. Apply filtering heuristics and user confirmation:

**Auto-filter candidates** (suggest skipping):
- **Attendance Status**: Meetings you declined or marked tentative
- **Broadcast Events**: Titles containing "Week", "Office Hours", "All Hands", "Town Hall"
- **Personal Time Slots**: Titles containing "Prep", "Lunch", "Focus Time", "Do Not Schedule", "OOO"
- **Large Group Events**: >15 attendees (likely broadcast/informational)

**Interactive Selection**:
Use `AskUserQuestion` to present filtered calendar events and let the user select which meetings to create documents for.

For each meeting, show:
- **Meeting title**
- **Time and duration**
- **Attendee count**
- **Attendance status** (accepted, tentative, declined)
- **Has artifacts** (agenda doc, Gemini transcript available)

**Three-tier handling**:
1. **Full document**: Create meeting file with all metadata and link from daily note
2. **Artifact links only**: Add just the artifact links (agenda, transcript, recording) to daily note's "Meeting Resources" section
3. **Skip entirely**: Don't reference in daily note

### 4. Determine File Paths

Use the `obsidian_date_formatter.py` script to generate correct paths based on Obsidian's date format configuration:

```bash
# Daily note path
uv run obsidian_date_formatter.py \
  --vault-path /path/to/vault \
  --date 2026-02-25 \
  --type daily \
  --json

# Meeting file path
uv run obsidian_date_formatter.py \
  --vault-path /path/to/vault \
  --date 2026-02-25 \
  --type meeting \
  --title "Meeting Title" \
  --json
```

This script:
- Reads `.obsidian/daily-notes.json` for date format configuration
- Converts moment.js format tokens to Python strftime
- Handles directory structure and filename generation

**Daily note**: Path structure determined by `.obsidian/daily-notes.json` format configuration
**Meeting files**: Path structure determined by CLAUDE.md conventions and date format

### 5. Match Attendees to People Files

For each calendar attendee:
1. Search `{people.folder}/` directory for matching files
2. Match by email (from frontmatter) or name (from filename)
3. Create quoted wikilinks: `"[[First Last]]"`

### 6. Create/Update Meeting Files

For each calendar event, create meeting file with:

**Frontmatter**:
```yaml
---
attendees:
  - "[[Person Name]]"
tags:
  - Meetings
created: YYYY-MM-DD HH:MM
URL:
gmeet: <hangout_link from calendar>
agenda: <google doc URL if attachment exists>
---
```

**Body sections**:
- `## Actions` - Empty
- `## Agenda` - Calendar event description (if any)
- `## Recent Meetings` - Base query block (from template)

### 7. Update Daily Note

Add/update sections in the daily note:

**Full meeting documents** - `# 📅 Meetings` section with wikilinks:
```markdown
# 📅 Meetings

- [[YYYY-MM-DD - Meeting Title 1]]
- [[YYYY-MM-DD - Meeting Title 2]]
```

**Artifact-only links** - `## Meeting Resources` section:
```markdown
## Meeting Resources

### OpenShift Week 2026 | Day 3
- [Agenda](https://docs.google.com/document/d/...)
- [Gemini Summary](https://meet.google.com/...)

### Atlassian Cloud Office Hours: UAT
- [Recording](https://drive.google.com/file/d/...)
```

## Calendar Event Field Mapping

| Calendar Field | Obsidian Property | Notes |
|---------------|-------------------|-------|
| `summary` | File name | Sanitized for filesystem |
| `attendees[].email` | `attendees` | Matched to PEOPLE/ files |
| `attendees[].responseStatus` | - | Used for filtering (accepted/declined/tentative/needsAction) |
| `hangoutLink` | `gmeet` | Google Meet URL |
| `description` | `## Agenda` content | Raw text from event |
| `attachments[].fileUrl` | `agenda`, `minutes`, `recording`, `transcript` | Based on title/type heuristics |
| `start.dateTime` | `start` | Meeting start time |
| `end.dateTime` | `end` | Meeting end time |

## Attachment Classification

Google Doc attachments:
- Title contains "notes", "agenda", "1:1", "1-1" → `agenda` property
- Title contains "minutes", "summary", "recap" → `minutes` property
- Default → `agenda` property

Google Meet artifacts:
- Gemini meeting transcript → `transcript` property
- Meeting recording → `recording` property

## File Naming

**Meeting title sanitization**:
- Replace `/`, `:`, `|` with ` - ` or remove
- Trim whitespace
- Example: "James : Deepika 1:1" → "James Deepika 1-1"

## Meeting Filtering Logic

### Skip Criteria (Auto-suggest)

**Attendance Status**:
- `responseStatus: "declined"` - You declined the meeting
- `responseStatus: "needsAction"` - You haven't responded (treat as tentative)
- `responseStatus: "tentative"` - You're tentative

**Broadcast Event Keywords** (in title):
- "Week", "Office Hours", "All Hands", "Town Hall", "Webinar", "Training"
- Events with >15 attendees

**Personal Time Keywords** (in title):
- "Prep", "Lunch", "Break", "Focus Time", "Focus Block"
- "Do Not Schedule", "DNS", "OOO", "Out of Office"
- "Personal", "Appointment", "Hold"

### AskUserQuestion Format

Present meetings grouped by recommendation:

**Recommended to document** (accepted, <8 attendees, has artifacts):
- James : Deepika 1:1 (10:00-10:30, 2 attendees) ✓ Accepted, Has agenda

**Consider skipping** (broadcast events, large groups):
- OpenShift Week 2026 | Day 3 (10:00-11:00, 50+ attendees) - Broadcast event
- Atlassian Cloud Office Hours (14:00-14:30, 20 attendees) - Office hours

**Definitely skip** (declined, personal time):
- Managed Regional Platform sync (10:30-11:00) ✗ Declined
- Prep Lunch (12:00-12:25) - Personal time

User selects from three options per meeting:
1. **Create document** - Full meeting file with metadata
2. **Link artifacts only** - Add to Meeting Resources section if artifacts exist
3. **Skip** - Don't reference in daily note

## Directory Structure

```
DAILY_NOTES/
└── YYYY/
    └── MM-Month/
        └── YYYY-MM-DD DayOfWeek.md

MEETINGS/
└── YYYY/
    └── MM-Month/
        └── YYYY-MM-DD - Title.md

PEOPLE/
└── First Last.md
```

## Templates Reference

**Daily Note Template**: `TEMPLATES/Daily Note Template.md`
**Meeting Template**: `TEMPLATES/Meeting Template.md`
**People Template**: `TEMPLATES/People Template.md`

## Update Behavior (Idempotency)

When run multiple times during the day:

**New meetings detected:**
- Create new meeting files
- Add wikilinks to daily note's `# 📅 Meetings` section

**Existing meetings with updated metadata:**
- Update `gmeet` property if changed
- Update `agenda`/`minutes` properties with new attachments
- Append new description content to `## Agenda` section (don't overwrite user notes)

**New attachments on existing meetings:**
- Add new Google Docs links (notes, agenda)
- Add Gemini transcript links when available
- Add recording links when available

**What NOT to overwrite:**
- User-added content in `## Agenda` section
- User-added content in `## Actions` section
- Any manual edits to frontmatter (only add missing properties)
