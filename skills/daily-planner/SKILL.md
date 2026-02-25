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
- Create meeting note files for each calendar event
- Enrich meeting files with Google Meet links, descriptions, and document attachments
- Link all meetings from the daily note

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

### 3. Determine File Paths (using discovered config)

**Daily note**: `{daily_notes.folder}/{format}/YYYY-MM-DD DayOfWeek.md`
**Meeting files**: `{meetings.folder}/{format}/YYYY-MM-DD - <Title>.md`

### 4. Match Attendees to People Files

For each calendar attendee:
1. Search `{people.folder}/` directory for matching files
2. Match by email (from frontmatter) or name (from filename)
3. Create quoted wikilinks: `"[[First Last]]"`

### 5. Create/Update Meeting Files

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

### 6. Update Daily Note

Add/update `# 📅 Meetings` section with wikilinks:
```markdown
# 📅 Meetings

- [[YYYY-MM-DD - Meeting Title 1]]
- [[YYYY-MM-DD - Meeting Title 2]]
```

## Calendar Event Field Mapping

| Calendar Field | Obsidian Property | Notes |
|---------------|-------------------|-------|
| `summary` | File name | Sanitized for filesystem |
| `attendees[].email` | `attendees` | Matched to PEOPLE/ files |
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
