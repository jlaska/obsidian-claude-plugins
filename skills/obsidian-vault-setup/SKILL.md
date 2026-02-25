---
name: obsidian-vault-setup
description: Bootstrap Obsidian vault conventions - checks for required folders and templates, creates missing items with sensible defaults
user-invocable: true
allowed-tools: Read, Glob, Bash, Write, AskUserQuestion
---

# Obsidian Vault Setup

This skill helps bootstrap an Obsidian vault with standard productivity conventions. It checks for required folders and templates, then creates missing items with sensible defaults.

## When to Use

Invoke `/obsidian-vault-setup` when:
- Setting up a new Obsidian vault for use with Claude Code
- A vault is missing expected conventions (PEOPLE/, MEETINGS/, etc.)
- You need to verify vault structure before running other skills
- You want to ensure templates exist for daily planning workflows

## Workflow

### 1. Discover Vault Root

Use the same logic as `obsidian-vault-discovery` skill:

```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Parse the JSON to find:
- The vault with `"open": true`, OR
- If in a vault directory (cwd contains `.obsidian/`), use that vault
- Otherwise, use the most recently opened vault (highest `ts`)

### 2. Check Required Conventions

Check for the existence of these items:

#### Folders

| Folder | Detection Method | Description |
|--------|------------------|-------------|
| `PEOPLE/` | Directory exists | People notes directory |
| `MEETINGS/` | Directory exists | Meeting notes directory |
| `DAILY_NOTES/` | Read `.obsidian/daily-notes.json` → `folder` field, OR directory exists | Daily notes directory |
| `TEMPLATES/` | Read `.obsidian/templates.json` → `folder` field, OR directory exists | Templates directory |

#### Templates

| Template | Path | Description |
|----------|------|-------------|
| Daily Note Template | `TEMPLATES/Daily Note Template.md` | Daily note template with Templater support |
| Meeting Template | `TEMPLATES/Meeting Template.md` | Meeting note template with attendees and dataview |
| People Template | `TEMPLATES/People Template.md` | People note template with contact fields |

#### Configuration

| File | Path | Description |
|------|------|-------------|
| CLAUDE.md | `CLAUDE.md` (vault root) | Vault conventions documentation for Claude |

### 3. Report Status

Display a status report showing:

```
✓ PEOPLE/ folder
✓ MEETINGS/ folder
✗ DAILY_NOTES/ folder (missing)
✓ TEMPLATES/ folder
✓ TEMPLATES/Daily Note Template.md
✗ TEMPLATES/Meeting Template.md (missing)
✗ TEMPLATES/People Template.md (missing)
✗ CLAUDE.md (missing)
```

### 4. Prompt for Creation

If any items are missing, ask the user:

```
The following items are missing from your vault:
- DAILY_NOTES/ folder
- TEMPLATES/Meeting Template.md
- TEMPLATES/People Template.md
- CLAUDE.md

Would you like to create these items with default conventions?
```

Use `AskUserQuestion` to confirm.

### 5. Create Missing Items

If the user confirms, create missing items:

#### Create Folders

```bash
mkdir -p "<vault_root>/PEOPLE"
mkdir -p "<vault_root>/MEETINGS"
mkdir -p "<vault_root>/DAILY_NOTES"
mkdir -p "<vault_root>/TEMPLATES"
```

#### Copy Templates

Copy template files from this skill's `defaults/templates/` directory to the vault's `TEMPLATES/` folder:

```bash
cp "skills/obsidian-vault-setup/defaults/templates/Daily Note Template.md" \
   "<vault_root>/TEMPLATES/Daily Note Template.md"

cp "skills/obsidian-vault-setup/defaults/templates/Meeting Template.md" \
   "<vault_root>/TEMPLATES/Meeting Template.md"

cp "skills/obsidian-vault-setup/defaults/templates/People Template.md" \
   "<vault_root>/TEMPLATES/People Template.md"
```

#### Copy CLAUDE.md Example

```bash
cp "skills/obsidian-vault-setup/defaults/CLAUDE.md.example" \
   "<vault_root>/CLAUDE.md"
```

### 6. Verify Creation

After creating items, re-run the status check to confirm all items now exist. Display final status:

```
✓ All required conventions are now in place!

Your vault is ready for:
- /daily-planner skill
- Meeting note automation
- People directory management
```

## Configuration Files to Check

### Daily Notes Configuration

Read `.obsidian/daily-notes.json` to get the configured folder:

```json
{
  "folder": "DAILY_NOTES",
  "format": "YYYY/MM-MMMM/YYYY-MM-DD dddd",
  "template": "TEMPLATES/Daily Note Template"
}
```

If this file doesn't exist, check if `DAILY_NOTES/` folder exists as fallback.

### Templates Configuration

Read `.obsidian/templates.json` to get the configured folder:

```json
{
  "folder": "TEMPLATES"
}
```

If this file doesn't exist, check if `TEMPLATES/` folder exists as fallback.

## Default Conventions

The skill installs these conventions:

1. **ALL-CAPS folder names**: `PEOPLE/`, `MEETINGS/`, `DAILY_NOTES/`, `TEMPLATES/`
2. **Templater-compatible templates**: Use `<% tp.* %>` syntax
3. **YAML frontmatter**: All notes use frontmatter with quoted wikilinks
4. **Date-based organization**: Meetings and daily notes organized by `YYYY/MM-Month/`
5. **Dataview queries**: Templates include dataview queries for related notes

## Idempotency

Running this skill multiple times is safe:
- Existing folders are not modified
- Existing templates are not overwritten
- Only missing items are created
- User confirmation required before any changes

## Usage Example

```
User: /obsidian-vault-setup

Claude:
Checking your Obsidian vault conventions...

Vault: /Users/jlaska/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault

Status:
✓ PEOPLE/ folder
✓ MEETINGS/ folder
✗ DAILY_NOTES/ folder (missing)
✓ TEMPLATES/ folder
✗ TEMPLATES/Daily Note Template.md (missing)
✗ TEMPLATES/Meeting Template.md (missing)
✗ TEMPLATES/People Template.md (missing)
✓ CLAUDE.md

The following items are missing:
- DAILY_NOTES/ folder
- TEMPLATES/Daily Note Template.md
- TEMPLATES/Meeting Template.md
- TEMPLATES/People Template.md

Would you like to create these items?

User: yes

Claude:
Creating missing items...
✓ Created DAILY_NOTES/ folder
✓ Created TEMPLATES/Daily Note Template.md
✓ Created TEMPLATES/Meeting Template.md
✓ Created TEMPLATES/People Template.md

All conventions are now in place! Your vault is ready for:
- /daily-planner skill
- Meeting note automation
- People directory management
```

## Related Skills

- **obsidian-vault-discovery**: Used to discover vault configuration
- **daily-planner**: Requires these conventions to be in place
