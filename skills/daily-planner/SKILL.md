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
gog calendar events --today --json > /tmp/calendar_events.json
```

### 3. Process Calendar Events

Run the Python script to create meeting notes and update daily note:

```bash
python3 process_calendar.py <vault_root> <calendar_json_path> [date]
```

The script handles:
- Auto-filtering non-meeting events
- Matching attendees to People files
- Creating meeting note files
- Updating the daily note

The script performs these steps automatically:

#### 3.1. Auto-Filter Meetings

Filter out non-meeting calendar events:

**Skip entirely** (not real meetings):
- **Working location events**: `eventType: "workingLocation"`
- **Declined meetings**: Your `responseStatus: "declined"`
- **No attendees**: Events with only yourself or no attendees
- **Broadcast events**: Events with `guestsCanSeeOtherGuests: false` and `guestsCanInviteOthers: false`

**Create meeting notes for**:
- Accepted meetings with other attendees
- Recurring team syncs
- 1:1 meetings
- Any event with multiple real participants

#### 3.2. Determine File Paths

**Daily note**: `{daily_notes.folder}/{format}/YYYY-MM-DD DayOfWeek.md`
**Meeting files**: `{meetings.folder}/{format}/YYYY-MM-DD - <Title>.md`

Path format from `.obsidian/daily-notes.json`:
- Format: `YYYY/MM-MMMM/YYYY-MM-DD dddd`
- Example daily note: `DAILY_NOTES/2026/02-February/2026-02-25 Wednesday.md`
- Example meeting: `MEETINGS/2026/02-February/2026-02-25 - Team Sync.md`

#### 3.3. Match Attendees to People Files

Match each calendar attendee using this cascade:

1. **Email match** - Search PEOPLE/ files for matching email in frontmatter
2. **Name match** - Match by filename (case-insensitive)
3. **Google Directory fallback** - Use `gog people search --email <email> --json`
4. **Display name** - Use the calendar display name

Output: `"[[Joshua Packer]]"` (quoted wikilink)

#### 3.4. Create/Update Meeting Files

For each meeting, create file with:

**Frontmatter**:
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
transcript: <gemini transcript URL>
URL: <calendar event link>
---
```

**Body sections**:
- `## Actions` - Empty
- `## Agenda` - Calendar event description (if any)
- `## Recent Meetings` - Base query block

#### 3.5. Update Daily Note

Add/update `# 📅 Meetings` section:

```markdown
# 📅 Meetings

- [[YYYY-MM-DD - Meeting Title 1]]
- [[YYYY-MM-DD - Meeting Title 2]]
```

Merges new meetings with existing links when run multiple times.

## Calendar Event Field Mapping

| Calendar Field | Obsidian Property | Resolution Method | Notes |
|---------------|-------------------|-------------------|-------|
| `summary` | File name | - | Sanitized for filesystem |
| `attendees[].email` | `attendees` | 1. Local grep for email in frontmatter<br/>2. Filename match<br/>3. `gog people search <email>` | Google Directory fallback |
| `attendees[].responseStatus` | - | - | Used for filtering (accepted/declined/tentative/needsAction) |
| `hangoutLink` | `gmeet` | - | Google Meet URL |
| `description` | `## Agenda` content | - | Raw text from event |
| `attachments[].fileUrl` | `agenda`, `minutes`, `recording`, `transcript` | - | Based on title/type heuristics |
| `start.dateTime` | `start` | - | Meeting start time |
| `end.dateTime` | `end` | - | Meeting end time |

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

Events are automatically filtered based on these criteria:

### Skip Automatically

**Event Type**:
- `eventType: "workingLocation"` - Office/location tracking events

**Attendance Status**:
- `responseStatus: "declined"` - You declined the meeting

**No Real Attendees**:
- No `attendees` field
- Only attendee is yourself (no `non_self_attendees`)

**Broadcast/Informational Events**:
- `guestsCanSeeOtherGuests: false` AND `guestsCanInviteOthers: false`
- Indicates one-way broadcast (e.g., company-wide events, office hours)

### Create Meeting Notes

All other events with:
- Multiple participants (you + at least one other person)
- Accepted or tentative status
- Real meeting interaction expected

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
