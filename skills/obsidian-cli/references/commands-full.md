# Obsidian CLI — Full Command Reference

Complete parameter documentation for every `obsidian` command, organized by category.

**Syntax**: `obsidian <command> [param=value ...] [--flag ...]`

**Global options**:
- `vault=VaultName` — target a specific vault (default: vault matching current directory)
- `--copy` — copy output to clipboard
- `format=json|md|csv|tsv` — output format (where supported)

---

## Vault & Version

```
obsidian version
```
Print the CLI and Obsidian app version.

```
obsidian vault
```
Show info about the current vault (name, path, plugin count).

---

## File Commands

### `create`
Create a new note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path relative to vault root (required) |
| `content` | string | Initial content; use `\n` for newlines |
| `open` | bool | Open in Obsidian after creation (default: false) |

```bash
obsidian create file="Notes/My Note.md" content="# Title\n\nBody text"
obsidian create file="Projects/New Project.md" open=true
```

### `read`
Read a note's content.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path relative to vault root (required) |
| `format` | string | `md` (default) or `json` |

```bash
obsidian read file="Notes/My Note.md"
obsidian read file="Notes/My Note.md" format=json
```

### `open`
Open a note in Obsidian's UI.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path relative to vault root (required) |
| `new-tab` | bool | Open in a new tab (default: false) |
| `new-window` | bool | Open in a new window |

```bash
obsidian open file="Meetings/2026/03-March/02-Monday/2026-03-02-Team Sync.md"
obsidian open file="Notes/My Note.md" new-tab=true
```

### `append`
Append content to the end of a note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path relative to vault root (required) |
| `content` | string | Content to append; `\n` for newlines (required) |

```bash
obsidian append file="Notes/My Note.md" content="\n## New Section\n\nParagraph text"
```

### `prepend`
Prepend content to the beginning of a note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path relative to vault root (required) |
| `content` | string | Content to prepend (required) |

```bash
obsidian prepend file="Notes/My Note.md" content="# Header\n\n"
```

### `move`
Move or rename a note (updates backlinks).

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Source path (required) |
| `target` | string | Destination path (required) |

```bash
obsidian move file="Old Name.md" target="New Folder/New Name.md"
```

### `delete`
Delete a note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path to delete (required) |
| `trash` | bool | Move to trash instead of permanent delete (default: true) |

```bash
obsidian delete file="Notes/Old Note.md"
obsidian delete file="Notes/Old Note.md" trash=false
```

### `info`
Get file metadata.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Path (required) |
| `format` | string | `json` or `md` (default) |

Returns: size, modified date, created date, tags, links, frontmatter properties.

```bash
obsidian info file="Notes/My Note.md" format=json
```

### `files`
List files in a folder.

| Parameter | Type | Description |
|-----------|------|-------------|
| `folder` | string | Folder path (default: vault root) |
| `recursive` | bool | Include subfolders (default: false) |
| `format` | string | `paths`, `json`, or `md` |

```bash
obsidian files folder="Meetings/2026" recursive=true
obsidian files folder="People/" format=json
```

### `folders`
List folders in the vault.

| Parameter | Type | Description |
|-----------|------|-------------|
| `folder` | string | Start from this folder (default: vault root) |
| `recursive` | bool | Include subfolders (default: true) |

```bash
obsidian folders
obsidian folders folder="Meetings/"
```

---

## Daily Note Commands

### `daily`
Open today's daily note in Obsidian's UI (creates it if it doesn't exist).

```bash
obsidian daily
```

### `daily:path`
Return the file path of today's daily note.

```bash
obsidian daily:path
# → daily/2026/03-March/2026-03-02-Monday.md
```

### `daily:read`
Read today's daily note content.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `md` (default) or `json` |

```bash
obsidian daily:read
obsidian daily:read format=json
```

### `daily:append`
Append content to today's daily note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `content` | string | Content to append (required); `\n` for newlines |

```bash
obsidian daily:append content="- [ ] Follow up on proposal"
obsidian daily:append content="\n## Notes\n\nSomething important"
```

### `daily:prepend`
Prepend content to today's daily note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `content` | string | Content to prepend (required) |

```bash
obsidian daily:prepend content="# Quick capture\n\n"
```

---

## Search Commands

### `search`
Full-text and property search using Obsidian's search index.

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Search query (required) |
| `path` | string | Restrict to this path prefix |
| `limit` | int | Max results (default: 50) |
| `format` | string | `paths` (default), `json`, or `md` |

**Query syntax:**
- `"phrase"` — exact phrase
- `tag:#tagname` — by tag
- `file:name` — by filename
- `path:folder/` — path prefix
- `property:value` — frontmatter property
- `-term` — exclude
- `term1 OR term2` — alternatives

```bash
obsidian search query="kubernetes"
obsidian search query='attendees:[[Kyle Lape]]' path="Meetings/"
obsidian search query="tag:#project -done" limit=20
obsidian search query="FIPS" format=json
```

### `search:context`
Search with surrounding context lines (like `grep -C`).

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Search query (required) |
| `path` | string | Restrict to path prefix |
| `context` | int | Lines of context around match (default: 2) |
| `limit` | int | Max results |

```bash
obsidian search:context query="managed services" path="Meetings/"
obsidian search:context query="FIPS" context=5
```

### `search:open`
Open search results in Obsidian's search panel.

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | string | Search query (required) |

```bash
obsidian search:open query="deployment strategy"
```

---

## Link Graph Commands

### `backlinks`
Find all notes that link to a given note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Target note path (required) |
| `format` | string | `paths` (default), `json`, `md` |
| `total` | bool | Return count only (default: false) |

```bash
obsidian backlinks file="People/Kyle Lape.md"
obsidian backlinks file="People/Kyle Lape.md" format=json
obsidian backlinks file="People/Kyle Lape.md" total=true
```

### `links`
Find all outgoing links from a note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Source note path (required) |
| `format` | string | `paths` (default), `json` |
| `unresolved` | bool | Include unresolved links (default: false) |

```bash
obsidian links file="Projects/Nested Virtualization.md"
obsidian links file="Projects/Nested Virtualization.md" unresolved=true
```

### `unresolved`
List all unresolved wiki-links (links pointing to nonexistent files).

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `paths`, `json`, `md` |
| `limit` | int | Max results |

```bash
obsidian unresolved
obsidian unresolved format=json
```

### `orphans`
Find notes with no incoming backlinks.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `paths` (default), `json` |
| `total` | bool | Return count only |
| `path` | string | Restrict to path prefix |

```bash
obsidian orphans
obsidian orphans path="ZK/" format=json
obsidian orphans total=true
```

### `deadends`
Find notes with no outgoing links.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `paths` (default), `json` |
| `path` | string | Restrict to path prefix |
| `total` | bool | Return count only |

```bash
obsidian deadends
obsidian deadends path="Reference/"
```

---

## Tag Commands

### `tags`
List all tags in the vault.

| Parameter | Type | Description |
|-----------|------|-------------|
| `counts` | bool | Include usage counts (default: false) |
| `sort` | string | `alpha` (default) or `count` |
| `format` | string | `md` or `json` |

```bash
obsidian tags
obsidian tags counts sort=count
obsidian tags format=json
```

### `tag`
Get info about a specific tag.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Tag name without `#` (required) |
| `verbose` | bool | Include list of files using this tag |
| `format` | string | `md` or `json` |

```bash
obsidian tag name="project"
obsidian tag name="project" verbose=true
obsidian tag name="project" format=json
```

---

## Property Commands

### `properties`
List all frontmatter properties used across the vault.

| Parameter | Type | Description |
|-----------|------|-------------|
| `by` | string | `property` (default) or `file` |
| `format` | string | `md` or `json` |

```bash
obsidian properties
obsidian properties by=file
obsidian properties format=json
```

### `property:read`
Read a specific property from a file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `name` | string | Property name (required) |

```bash
obsidian property:read file="Notes/My Note.md" name="status"
```

### `property:set`
Set a frontmatter property value.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `name` | string | Property name (required) |
| `value` | string | New value (required) |

```bash
obsidian property:set file="Notes/My Note.md" name="status" value="done"
```

### `property:remove`
Remove a frontmatter property.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `name` | string | Property name to remove (required) |

```bash
obsidian property:remove file="Notes/My Note.md" name="draft"
```

### `aliases`
List all aliases defined across the vault.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `md` or `json` |

```bash
obsidian aliases
obsidian aliases format=json
```

---

## Task Commands

### `tasks`
List tasks across the vault.

| Parameter | Type | Description |
|-----------|------|-------------|
| `todo` | flag | Show only incomplete tasks |
| `done` | flag | Show only completed tasks |
| `file` | string | Restrict to a specific file |
| `daily` | flag | Restrict to today's daily note |
| `verbose` | bool | Include file path and line number |
| `format` | string | `md` or `json` |
| `limit` | int | Max results |

```bash
obsidian tasks todo
obsidian tasks done
obsidian tasks todo verbose=true
obsidian tasks file="Projects/Nested Virtualization.md"
obsidian tasks daily
obsidian tasks todo format=json
```

### `task`
Interact with a specific task.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path containing the task (required) |
| `ref` | int | Line number of the task (required) |
| `toggle` | bool | Toggle completion status |
| `status` | string | Set status: `todo`, `done`, `cancelled` |

```bash
obsidian task file="Notes/My Note.md" ref=42 toggle=true
obsidian task file="Notes/My Note.md" ref=42 status=done
```

---

## Base Commands

### `bases`
List all Base files in the vault.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `paths` (default) or `json` |

```bash
obsidian bases
obsidian bases format=json
```

### `base:views`
List views defined in a Base file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Base file path (required) |

```bash
obsidian base:views file="jira/Bases/Active Issues.md"
```

### `base:query`
Query a Base and return results.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Base file path (required) |
| `view` | string | Specific view name (default: first view) |
| `format` | string | `json`, `csv`, `tsv`, `md`, `paths` |

```bash
obsidian base:query file="jira/Bases/Active Issues.md"
obsidian base:query file="jira/Bases/Active Issues.md" format=json
obsidian base:query file="jira/Bases/Active Issues.md" format=csv
obsidian base:query file="jira/Bases/Active Issues.md" format=paths
```

---

## Outline & Word Count

### `outline`
Get the heading structure of a note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `format` | string | `md` (default) or `json` |

```bash
obsidian outline file="Projects/Nested Virtualization.md"
obsidian outline file="Projects/Nested Virtualization.md" format=json
```

### `wordcount`
Get word count for a note or folder.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path |
| `folder` | string | Folder path (counts all files) |
| `format` | string | `md` or `json` |

```bash
obsidian wordcount file="docs/Architecture.md"
obsidian wordcount folder="Meetings/2026" format=json
```

---

## History & Version Commands

### `history`
List version history for a file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `limit` | int | Number of versions (default: 10) |
| `format` | string | `md` or `json` |

```bash
obsidian history file="Projects/Nested Virtualization.md"
obsidian history file="Notes/My Note.md" limit=20 format=json
```

### `history:read`
Read a specific historical version.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `version` | int | Version number from `history` output (required) |

```bash
obsidian history:read file="Notes/My Note.md" version=3
```

### `diff`
Show diff between current and a historical version.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `version` | int | Historical version to diff against (required) |

```bash
obsidian diff file="Notes/My Note.md" version=1
```

### `history:restore`
Restore a file to a historical version.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Note path (required) |
| `version` | int | Version to restore (required) |

```bash
obsidian history:restore file="Notes/My Note.md" version=2
```

---

## App Control Commands

### `commands`
List all available command palette commands.

| Parameter | Type | Description |
|-----------|------|-------------|
| `filter` | string | Filter by name substring |
| `format` | string | `md` or `json` |

```bash
obsidian commands
obsidian commands filter="toggle" format=json
```

### `command`
Run a command palette command by ID.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Command ID from `commands` output (required) |

```bash
obsidian command id="editor:toggle-bold"
obsidian command id="app:reload"
```

### `workspaces`
List saved workspaces.

```bash
obsidian workspaces
```

### `workspace`
Switch to a saved workspace.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Workspace name (required) |

```bash
obsidian workspace name="Research"
```

### `tabs`
List open tabs.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `md` or `json` |

```bash
obsidian tabs
obsidian tabs format=json
```

### `tab:close`
Close a tab.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Close tab showing this file |
| `active` | flag | Close the currently active tab |

```bash
obsidian tab:close file="Notes/My Note.md"
obsidian tab:close --active
```

### `recents`
List recently opened files.

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Number of files (default: 10) |
| `format` | string | `paths`, `json` |

```bash
obsidian recents
obsidian recents limit=20 format=json
```

### `reload`
Soft reload Obsidian (preserves vault, reloads plugins).

```bash
obsidian reload
```

### `restart`
Restart the Obsidian app.

```bash
obsidian restart
```

---

## Plugin Commands

### `plugins`
List all plugins and their status.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `md` or `json` |

```bash
obsidian plugins
obsidian plugins format=json
```

### `plugin:enable`
Enable a plugin.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Plugin ID (required) |

```bash
obsidian plugin:enable id="dataview"
```

### `plugin:disable`
Disable a plugin.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Plugin ID (required) |

```bash
obsidian plugin:disable id="templater-obsidian"
```

### `plugin:install`
Install a community plugin.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Plugin ID from community list (required) |

```bash
obsidian plugin:install id="obsidian-tasks-plugin"
```

### `plugin:uninstall`
Uninstall a plugin.

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Plugin ID (required) |

```bash
obsidian plugin:uninstall id="old-plugin"
```

### `plugin:reload`
Reload a plugin (useful during development).

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string | Plugin ID (required) |

```bash
obsidian plugin:reload id="dataview"
```

---

## Theme Commands

### `themes`
List installed themes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `md` or `json` |

```bash
obsidian themes
```

### `theme:set`
Set the active theme.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Theme name (required) |

```bash
obsidian theme:set name="Minimal"
```

### `theme:install`
Install a community theme.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Theme name from community list (required) |

```bash
obsidian theme:install name="Minimal"
```

### `theme:uninstall`
Uninstall a theme.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Theme name (required) |

```bash
obsidian theme:uninstall name="Old Theme"
```

---

## Snippet Commands

### `snippets`
List CSS snippets and their enabled status.

```bash
obsidian snippets
```

### `snippet:enable`
Enable a CSS snippet.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Snippet filename without `.css` (required) |

```bash
obsidian snippet:enable name="my-custom-styles"
```

### `snippet:disable`
Disable a CSS snippet.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Snippet filename without `.css` (required) |

```bash
obsidian snippet:disable name="my-custom-styles"
```

---

## Template Commands

### `templates`
List available templates.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | `paths` (default) or `json` |

```bash
obsidian templates
```

### `template:read`
Read a template's content.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Template path (required) |
| `resolve` | bool | Resolve Templater variables (default: false) |

```bash
obsidian template:read file="Templates/Meeting.md"
obsidian template:read file="Templates/Meeting.md" resolve=true
```

### `template:insert`
Insert a template into the active note.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Template path (required) |

```bash
obsidian template:insert file="Templates/Meeting.md"
```

---

## Developer Commands

### `devtools`
Open Obsidian DevTools.

```bash
obsidian devtools
```

### `dev:screenshot`
Take a screenshot of the Obsidian window.

| Parameter | Type | Description |
|-----------|------|-------------|
| `output` | string | Output file path (default: clipboard or temp file) |

```bash
obsidian dev:screenshot
obsidian dev:screenshot output="$HOME/.cache/obsidian-claude-plugins/obsidian-screen.png"
```

### `dev:console`
Read the developer console log.

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Number of recent entries (default: 50) |
| `level` | string | `log`, `warn`, `error` (default: all) |

```bash
obsidian dev:console
obsidian dev:console level=error limit=20
```

### `dev:errors`
Get recent JavaScript errors.

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Number of errors |

```bash
obsidian dev:errors
```

### `eval`
Evaluate JavaScript in Obsidian's context.

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | JavaScript expression or statement (required) |

```bash
obsidian eval code="app.vault.getName()"
obsidian eval code="app.workspace.getActiveFile()?.path"
obsidian eval code="Object.keys(app.plugins.plugins).join('\n')"
```

### `dev:css`
Get computed CSS for a DOM element.

| Parameter | Type | Description |
|-----------|------|-------------|
| `selector` | string | CSS selector (required) |

```bash
obsidian dev:css selector=".workspace-leaf"
```

### `dev:dom`
Inspect DOM structure.

| Parameter | Type | Description |
|-----------|------|-------------|
| `selector` | string | CSS selector (required) |
| `depth` | int | Depth of DOM tree to show (default: 3) |

```bash
obsidian dev:dom selector=".workspace"
obsidian dev:dom selector=".nav-files-container" depth=5
```
