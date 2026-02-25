---
name: obsidian-vault-discovery
description: Discover Obsidian vault configuration - reads .obsidian settings to determine folder paths, templates, and conventions
user-invocable: false
allowed-tools: Read, Glob, Grep
---

# Obsidian Vault Discovery

This skill discovers configuration from an Obsidian vault by reading `.obsidian/` settings files and `CLAUDE.md` conventions.

## When to Use

Invoke this skill (internally) when you need to determine:
- Vault location and root path
- Daily notes folder and date format
- Templates folder location
- Meeting notes folder (from CLAUDE.md conventions)
- People directory (from CLAUDE.md conventions)

## Discovery Steps

### 1. Find Vault Root

Read Obsidian's application config to get registered vault paths:

**Primary method** - Read from macOS Application Support:
```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Returns:
```json
{
  "vaults": {
    "<vault_id>": {
      "path": "/Users/jlaska/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault",
      "ts": 1695397882102,
      "open": true
    }
  }
}
```

**Logic**:
1. Parse `obsidian.json` to get all vault paths
2. If multiple vaults, prefer the one with `"open": true`
3. If in a vault directory (cwd contains `.obsidian/`), use that vault
4. Otherwise, use the most recently opened vault (highest `ts`)

### 2. Read Core Configuration

**Daily Notes** (`.obsidian/daily-notes.json`):
```json
{
  "template": "TEMPLATES/Daily Note Template",
  "folder": "DAILY_NOTES",
  "format": "YYYY/MM-MMMM/YYYY-MM-DD dddd"
}
```

**Templates** (`.obsidian/templates.json`):
```json
{
  "folder": "TEMPLATES"
}
```

### 3. Read Plugin Configuration

**Templater** (`.obsidian/plugins/templater-obsidian/data.json`):
- `templates_folder`: Templates directory
- `user_scripts_folder`: Scripts directory
- `folder_templates`: Auto-filing rules

### 4. Read Vault Conventions

Check `CLAUDE.md` in vault root for:
- Directory structure (e.g., MEETINGS/, PEOPLE/, BOOKS/)
- File naming patterns
- Frontmatter standards

### 5. Output Configuration Object

Return discovered configuration:
```yaml
vault_root: /path/to/vault
daily_notes:
  folder: DAILY_NOTES
  format: "YYYY/MM-MMMM/YYYY-MM-DD dddd"
  template: TEMPLATES/Daily Note Template.md
meetings:
  folder: MEETINGS
  format: "YYYY/MM-Month"
  template: TEMPLATES/Meeting Template.md
people:
  folder: PEOPLE
templates:
  folder: TEMPLATES
scripts:
  folder: SCRIPTS
```

## Configuration Sources

| Setting | Primary Source | Fallback |
|---------|---------------|----------|
| Daily notes folder | `.obsidian/daily-notes.json` | CLAUDE.md |
| Daily notes format | `.obsidian/daily-notes.json` | CLAUDE.md |
| Templates folder | `.obsidian/templates.json` | CLAUDE.md |
| Meetings folder | CLAUDE.md | Convention (MEETINGS/) |
| People folder | CLAUDE.md | Convention (PEOPLE/) |

## Usage by Other Skills

Other skills (like `daily-planner`) should invoke this skill first to get configuration, then use the discovered paths rather than hardcoded values.
