---
name: obsidian-vault-discovery
description: Discover Obsidian vault configuration - uses obsidian-cli when available, falls back to reading .obsidian settings files
user-invocable: false
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# Obsidian Vault Discovery

This skill discovers configuration from an Obsidian vault. It uses the `obsidian-cli` (bundled with Obsidian 1.12+) as the primary method, falling back to direct file reads when Obsidian is not running.

## When to Use

Invoke this skill (internally) when you need to determine:
- Vault location and root path
- Daily notes folder and date format
- Templates folder location
- Meeting notes folder (from CLAUDE.md conventions)
- People directory (from CLAUDE.md conventions)

## Discovery Steps

### 1. Check CLI Availability

```bash
obsidian vault info=name
```

If this command succeeds, Obsidian is running and CLI-based discovery (steps 2–4) will work. If it fails (command not found or Obsidian not running), skip to the **Fallback: File-Based Discovery** section below.

> The `obsidian-cli` binary is at `/Applications/Obsidian.app/Contents/MacOS/obsidian-cli`. Ensure `/Applications/Obsidian.app/Contents/MacOS` is on the PATH, or use the full path.

### 2. Get Vault Root

```bash
obsidian vault info=path
```

Returns the absolute vault path, e.g.:
```
/Users/jlaska/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault
```

### 3. Read Plugin Configuration via eval

Fetch live plugin settings from the running Obsidian instance:

**Daily Notes** (core plugin):
```bash
obsidian eval "code=JSON.stringify(app.internalPlugins.getPluginById('daily-notes').instance.options)"
```

Returns:
```json
{"template":"TEMPLATES/Daily Note Template","folder":"DAILY_NOTES","format":"YYYY/MM-MMMM/YYYY-MM-DD dddd"}
```

**Templates** (core plugin):
```bash
obsidian eval "code=JSON.stringify(app.internalPlugins.getPluginById('templates').instance.options)"
```

Returns:
```json
{"folder":"TEMPLATES"}
```

**Templater** (community plugin — skip if not installed):
```bash
obsidian eval "code=JSON.stringify(app.plugins.getPlugin('templater-obsidian')?.settings)"
```

Returns settings including `templates_folder`, `user_scripts_folder`, and `folder_templates`.

### 4. Read Vault Conventions

Check `CLAUDE.md` in the vault root for vault-specific directory structure and naming conventions (this is not available via CLI):

```bash
cat "<vault_root>/CLAUDE.md"
```

Look for:
- Meetings folder (e.g., `MEETINGS/`)
- People directory (e.g., `PEOPLE/`)
- File naming patterns and frontmatter standards

### 5. Output Configuration Object

Return discovered configuration:
```yaml
vault_root: /path/to/vault
vault_name: Obsidian Vault
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

---

## Fallback: File-Based Discovery

Use this when `obsidian vault info=name` fails (Obsidian is not running or CLI is unavailable).

**Find vault root** — Read `~/Library/Application Support/obsidian/obsidian.json`:
```json
{
  "vaults": {
    "<vault_id>": {
      "path": "/path/to/vault",
      "ts": 1695397882102,
      "open": true
    }
  }
}
```

Prefer the vault with `"open": true`, then highest `ts`, or use cwd if it contains `.obsidian/`.

**Daily Notes** — Read `<vault_root>/.obsidian/daily-notes.json`

**Templates** — Read `<vault_root>/.obsidian/templates.json`

**Templater** — Read `<vault_root>/.obsidian/plugins/templater-obsidian/data.json`

**Vault conventions** — Read `<vault_root>/CLAUDE.md`

---

## Additional CLI Commands for Consuming Skills

These commands are available to consuming skills (like `daily-planner`) for further vault introspection:

| Command | Description |
|---------|-------------|
| `obsidian daily:path` | Absolute path to today's daily note |
| `obsidian folders` | All folder paths in the vault |
| `obsidian templates` | List available template names |
| `obsidian plugins:enabled` | List currently enabled plugins |
| `obsidian search keyword=<term>` | Full-text search across vault |

---

## Configuration Sources

| Setting | Primary Source (CLI) | Fallback (file) |
|---------|---------------------|-----------------|
| Vault root | `obsidian vault info=path` | `~/Library/.../obsidian.json` |
| Daily notes folder/format | `eval` daily-notes options | `.obsidian/daily-notes.json` |
| Templates folder | `eval` templates options | `.obsidian/templates.json` |
| Templater config | `eval` templater settings | `.obsidian/plugins/templater-obsidian/data.json` |
| Meetings folder | CLAUDE.md | Convention (MEETINGS/) |
| People folder | CLAUDE.md | Convention (PEOPLE/) |

## Usage by Other Skills

Other skills (like `daily-planner`) should invoke this skill first to get configuration, then use the discovered paths rather than hardcoded values.
