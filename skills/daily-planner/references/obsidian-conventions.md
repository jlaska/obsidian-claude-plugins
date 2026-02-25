# Obsidian Vault Conventions Reference

This file contains key conventions from the vault's CLAUDE.md for reference by the daily-planner skill.

## Directory Structure

This vault uses **ALL-CAPS folder names** as a convention:

- **PEOPLE/** - Individual person notes (colleagues, contacts)
- **MEETINGS/** - Meeting notes organized by year/month
  - Structure: `MEETINGS/YYYY/MM-Month/`
  - Example: `MEETINGS/2026/02-February/`
- **DAILY_NOTES/** - Daily journal entries organized by year/month
  - Structure: `DAILY_NOTES/YYYY/MM-Month/`
  - Example: `DAILY_NOTES/2026/02-February/`
- **TEMPLATES/** - Template files for creating new notes

## File Naming Conventions

### PEOPLE Directory
- Format: `First Last.md`
- Example: `Matt Hicks.md`, `Chris Moore.md`

### MEETINGS Directory
- Format: `YYYY-MM-DD - Title.md`
- Location: `MEETINGS/YYYY/MM-Month/`
- Example: `2026-02-16 - Weekly Sync.md` in `MEETINGS/2026/02-February/`

### DAILY_NOTES Directory
- Format: `YYYY-MM-DD DayOfWeek.md`
- Location: `DAILY_NOTES/YYYY/MM-Month/`
- Example: `2026-02-16 Sunday.md` in `DAILY_NOTES/2026/02-February/`

## Frontmatter Standards

**People Files:**
```yaml
---
company:
location:
email:
aliases:
tags:
  - People
---
```

**Meeting Files:**
```yaml
---
attendees:
  - "[[Person Name]]"
tags:
  - Meetings
---
```

**Daily Notes:**
```yaml
---
tags:
  - Daily_Notes
---
```

## Important Notes on Frontmatter

- **Tags:** Use established tags like `People`, `Meetings`, `Daily_Notes`
- **Wikilinks in YAML:** When referencing people or notes in YAML arrays (like `attendees`), use **quoted wikilinks**: `"[[Name]]"`

## Internal Linking

- Use wikilinks `[[Note Name]]` for all internal references
- Link to people from meetings, daily notes, and other contexts
- Dataview queries rely on these links to generate dynamic views

## Quoted Wikilinks in YAML

- When including wikilinks in YAML arrays, always quote them
- Correct: `"[[Matt Hicks]]"`
- Incorrect: `[[Matt Hicks]]`
- This ensures proper parsing by Obsidian and plugins
