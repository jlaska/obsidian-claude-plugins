---
name: obsidian-cli
description: |
  Interact with the running Obsidian app via the `obsidian` CLI. Use when the user wants to:
  - Control Obsidian from the terminal
  - Open files in Obsidian's UI
  - Manage plugins or themes
  - Run Obsidian command palette commands
  - Take screenshots of the Obsidian window
  - Use the developer console or evaluate JavaScript in Obsidian
  - Manage workspaces or tabs
  - View file version history or diffs
  - Inspect backlinks, orphans, or the link graph
  - Any direct interaction with the running Obsidian application
user-invocable: false
allowed-tools:
  - Bash
---

<!-- Source: https://github.com/jhjaggars/obsidian-cli-skill by Jesse Jaggars -->
<!-- Included locally with attribution. Review upstream for updates. -->

# Obsidian CLI Skill

The `obsidian` CLI (available in Obsidian 1.12+) lets you control the running Obsidian app from the terminal. It has awareness of the vault's search index, link graph, tags, and UI state — things that direct file access cannot provide.

Full command reference: `skills/obsidian-cli/references/commands-full.md`

---

## Prerequisites

1. **Obsidian 1.12+** must be installed and running
2. **CLI must be registered**: In Obsidian → Settings → General → scroll to "CLI" → enable it
3. **PATH must include the CLI binary**: Add to `~/.zprofile`:
   ```bash
   export PATH="$PATH:/Applications/Obsidian.app/Contents/MacOS"
   ```
   Then reload: `source ~/.zprofile`

### Verify setup

```bash
obsidian version
```

If this fails, walk through the prerequisites above. If the app is installed but CLI isn't on PATH:
```bash
/Applications/Obsidian.app/Contents/MacOS/obsidian version
```

---

## Core Syntax

```
obsidian <command> [param=value] [--flag]
```

- **Named parameters**: `obsidian read file="path/to/note.md"`
- **Vault targeting**: defaults to the vault matching the current directory; use `vault=VaultName` to override
- **Copy to clipboard**: append `--copy` to any command
- **Multiline content**: use `\n` for newlines, `\t` for tabs in `content=` params
- **Output formats**: many commands support `format=json`, `format=md`, `format=csv`

### File path conventions

Paths are relative to the vault root:
```bash
obsidian read file="Meetings/2026/03-March/02-Monday/2026-03-02-Team Sync.md"
```

---

## When to Use CLI vs Direct File Access

### Prefer CLI

| Task | CLI Command |
|------|-------------|
| Search with index/property awareness | `obsidian search query=X` |
| Find backlinks to a note | `obsidian backlinks file=X` |
| Find orphan or dead-end notes | `obsidian orphans`, `obsidian deadends` |
| Open a file in Obsidian's UI | `obsidian open file=X` |
| Aggregate tasks across vault | `obsidian tasks todo` |
| List/filter tags | `obsidian tags counts` |
| Run a command palette command | `obsidian command id=X` |
| Enable/disable a plugin | `obsidian plugin:enable id=X` |
| View file version history | `obsidian history file=X` |
| Diff against a previous version | `obsidian diff file=X` |
| Resolve template variables | `obsidian template:read file=X resolve=true` |
| Query a Base | `obsidian base:query file=X` |
| Take a UI screenshot | `obsidian dev:screenshot` |
| Evaluate JS in Obsidian context | `obsidian eval code=X` |

### Prefer direct file access (Read/Edit/Write)

| Task | Reason |
|------|--------|
| Surgical section-level editing | Edit tool gives precise control |
| Complex content creation | Write tool is more reliable for large content |
| Bulk file operations | Bash/Glob/Grep is faster |
| Obsidian is not running | CLI requires a running app |
| Frontmatter YAML manipulation | Edit tool handles quoting and indentation |
| Working with file structure/paths | Glob is faster than CLI for discovery |

### Use both (common pattern)

```
CLI for discovery → direct access for manipulation
```

Example: Find all meetings with a person (CLI search), then read/edit the specific file (Read/Edit tools).

---

## File & Folder Commands

```bash
# Create a new note
obsidian create file="Notes/My Note.md" content="# My Note\n\nContent here"

# Read a note's content
obsidian read file="Notes/My Note.md"

# Open a note in Obsidian's UI
obsidian open file="Notes/My Note.md"

# Append content to a note
obsidian append file="Notes/My Note.md" content="New paragraph\n\nMore text"

# Prepend content to a note
obsidian prepend file="Notes/My Note.md" content="## Added at top\n\n"

# Move or rename a note
obsidian move file="Old Name.md" target="New Name.md"

# Delete a note
obsidian delete file="Notes/Old Note.md"

# Get file metadata (size, modified date, links, tags)
obsidian info file="Notes/My Note.md"

# List files in a folder
obsidian files folder="Meetings/2026"

# List folders
obsidian folders
```

---

## App Control

```bash
# List all available command palette commands
obsidian commands

# Run a specific command by ID
obsidian command id="editor:toggle-bold"

# List workspaces
obsidian workspaces

# Switch to a workspace
obsidian workspace name="Research"

# List open tabs
obsidian tabs

# Close a tab
obsidian tab:close file="Notes/Something.md"

# Get recently opened files
obsidian recents

# Reload Obsidian (soft reload, preserves vault)
obsidian reload

# Restart Obsidian
obsidian restart
```

---

## Plugin Management

```bash
# List all plugins (installed, enabled status)
obsidian plugins

# Enable a plugin
obsidian plugin:enable id="dataview"

# Disable a plugin
obsidian plugin:disable id="dataview"

# Install a community plugin
obsidian plugin:install id="plugin-id"

# Uninstall a plugin
obsidian plugin:uninstall id="plugin-id"

# Reload a plugin (after code changes)
obsidian plugin:reload id="dataview"
```

---

## Theme & Snippet Management

```bash
# List installed themes
obsidian themes

# Set active theme
obsidian theme:set name="Minimal"

# Install a community theme
obsidian theme:install name="Minimal"

# List CSS snippets and their enabled status
obsidian snippets

# Enable a CSS snippet
obsidian snippet:enable name="my-snippet"

# Disable a CSS snippet
obsidian snippet:disable name="my-snippet"
```

---

## History & Versions

```bash
# View version history for a file
obsidian history file="Notes/My Note.md"

# Read a specific historical version
obsidian history:read file="Notes/My Note.md" version=2

# Diff current version against a historical version
obsidian diff file="Notes/My Note.md" version=1

# Restore a file to a historical version
obsidian history:restore file="Notes/My Note.md" version=3
```

---

## Developer Tools

```bash
# Open DevTools
obsidian devtools

# Take a screenshot of the Obsidian window
obsidian dev:screenshot

# Read the developer console log
obsidian dev:console

# Get recent JavaScript errors
obsidian dev:errors

# Evaluate JavaScript in Obsidian's context
obsidian eval code="app.vault.getName()"

# Get computed CSS for an element
obsidian dev:css selector=".workspace-leaf"

# Inspect DOM structure
obsidian dev:dom selector=".workspace"
```

---

## Templates

```bash
# List available templates
obsidian templates

# Read a template's raw content
obsidian template:read file="Templates/Meeting.md"

# Read a template with Templater variables resolved
obsidian template:read file="Templates/Meeting.md" resolve=true

# Insert a template into the active note
obsidian template:insert file="Templates/Meeting.md"
```

---

## Error Handling

- **"Obsidian is not running"**: Start Obsidian, then retry
- **"CLI not found"**: Check PATH setup (see Prerequisites); use full path `/Applications/Obsidian.app/Contents/MacOS/obsidian` as fallback
- **"CLI not registered"**: Go to Obsidian Settings → General → enable CLI
- **"No vault found"**: Run from within the vault directory, or add `vault=VaultName`
- **Command fails silently**: Try adding `format=json` to get structured error output

---

## Quick Reference

```bash
# Check version / confirm CLI works
obsidian version

# Vault info
obsidian vault

# Open today's daily note in UI
obsidian daily

# Search vault
obsidian search query="kubernetes"

# List all tags
obsidian tags counts sort=count
```

Full command reference with all parameters and flags: `skills/obsidian-cli/references/commands-full.md`
