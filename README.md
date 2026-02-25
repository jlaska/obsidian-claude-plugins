# Obsidian Claude Plugins

Obsidian vault integration skills for Claude Code.

## Overview

This repository contains Claude Code skills that integrate with Obsidian vaults to automate daily planning workflows and vault management.

## Skills

### 1. `daily-planner`

**User-invocable**: Yes (`/daily-planner`)

Automates daily planning by:
- Fetching today's Google Calendar events
- **Interactively filtering** meetings (skip broadcast events, declined meetings, personal time)
- Creating daily note with meeting links and artifact references
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

### 1. Add Marketplace

```bash
claude plugin marketplace add jlaska/obsidian-claude-plugins
```

### 2. Install Plugin

```bash
claude plugin install obsidian-productivity@obsidian-claude-plugins
```

The plugin is automatically enabled after installation.

### 3. Verify Installation

The `/daily-planner` skill should now be available in Claude Code.

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
