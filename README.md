# Obsidian Claude Plugins

Obsidian vault integration skills for Claude Code.

## Overview

This repository contains Claude Code skills that integrate with Obsidian vaults to automate daily planning workflows and vault management.

## Skills

### 1. `daily-planner`

**User-invocable**: Yes (`/daily-planner`)

Automates daily planning by:
- Fetching today's Google Calendar events
- Creating daily note with meeting links
- Creating meeting files with enriched metadata (Google Meet links, descriptions, attachments)
- Matching calendar attendees to People notes
- Supporting idempotent updates (safe to run multiple times)

**Usage**: `/daily-planner`

### 2. `obsidian-vault-discovery`

**User-invocable**: No (internal)

Discovers Obsidian vault configuration by reading:
- `.obsidian/` settings files
- `CLAUDE.md` conventions
- Returns vault paths, folder structure, and naming patterns

Used internally by other skills to determine vault configuration.

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/jlaska/obsidian-claude-plugins.git ~/Projects/obsidian-claude-plugins
```

### 2. Symlink to Claude Marketplaces

```bash
ln -s ~/Projects/obsidian-claude-plugins ~/.claude/plugins/marketplaces/obsidian-claude-plugins
```

### 3. Enable Plugin in Claude Settings

Add to `~/.claude/settings.json` under `enabledPlugins`:

```json
"obsidian-productivity@obsidian-claude-plugins": true
```

### 4. Restart Claude Code

The `/daily-planner` skill should now be available.

## Requirements

- **Claude Code** with plugin support
- **Obsidian** vault with `.obsidian/` settings
- **Google Calendar** CLI tool (`gog`) configured
- Vault conventions documented in `CLAUDE.md`

## Vault Conventions

The skills expect an Obsidian vault with:
- ALL-CAPS directory names (`PEOPLE/`, `MEETINGS/`, `DAILY_NOTES/`)
- YAML frontmatter with quoted wikilinks
- Date-based folder structure for meetings and daily notes
- Templates in `TEMPLATES/` directory

See `skills/daily-planner/references/obsidian-conventions.md` for details.

## License

MIT

## Author

James Laska (jlaska@redhat.com)
