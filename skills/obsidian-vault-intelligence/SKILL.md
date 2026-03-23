---
name: obsidian-vault-intelligence
description: |
  Smart vault operations powered by the Obsidian CLI and Dataview API. Use when the user wants to:
  - Run a vault health check (orphaned notes, dead links, unresolved references, tag stats)
  - Search the vault using Obsidian's indexed search (faster and smarter than grep)
  - Find vault connections for a topic using thematic/semantic search
  - Query notes using Dataview (when Dataview plugin is installed)
  - Find open tasks across all meeting notes or the full vault
  - Analyze the vault's link graph (what links to what, what's isolated)
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# Obsidian Vault Intelligence

Provides smart vault search, health reporting, and Dataview query access — all
powered by the `obsidian` CLI (Obsidian 1.12+). Falls back to direct file access
when the CLI is unavailable.

**Prerequisite**: The `obsidian-cli` skill must be loaded. Verify with:
```bash
obsidian version 2>/dev/null && echo "CLI available" || echo "CLI unavailable — will use fallbacks"
```

---

## Capability 1: Smart Vault Search

Use Obsidian's indexed search engine instead of grep to find thematically related
notes. The index understands tags, aliases, backlinks, and property values — not
just literal text.

### When to use

- Finding vault connections for a new note (youtube-summary, book notes, etc.)
- Locating notes about a person, project, or concept
- Searching by tag, frontmatter property, or link relationship

### How to search

```bash
# Basic full-text search
obsidian search query="<keywords>" format=json limit=20 2>/dev/null

# Tag-based search
obsidian search query="tag:#negotiation OR tag:#communication" format=json 2>/dev/null

# Search within a folder
obsidian search query="<keywords>" path="BOOKS/" format=json 2>/dev/null

# Find notes that link to a specific note
obsidian backlinks file="BOOKS/Never Split the Difference.md" format=json 2>/dev/null

# Parse JSON results for paths
obsidian search query="<keywords>" format=json 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); [print(r.get('path','')) for r in data]"
```

**Query syntax:**
- `term1 term2` — both terms
- `"exact phrase"` — phrase match
- `tag:#tagname` — by tag
- `term1 OR term2` — either term
- `-term` — exclude
- `property:value` — frontmatter property value
- `path:folder/` — restrict to path prefix

### Fallback when CLI unavailable

```bash
# Search BOOKS/ and PROJECTS/ with thematic keywords
grep -rl "<keyword>" "<vault_root>/BOOKS" "<vault_root>/PROJECTS" \
  --include="*.md" 2>/dev/null | head -20

# Search full vault
grep -rl "<keyword>" "<vault_root>" \
  --include="*.md" \
  --exclude-dir=SCRIPTS --exclude-dir=DATAVIEW_SCRIPTS \
  2>/dev/null | head -20
```

---

## Capability 2: Dataview API Bridge

When the Dataview plugin is installed, `obsidian eval` gives Claude direct access
to Dataview's JavaScript API — the most powerful way to query the vault.

### Check if Dataview is available

```bash
obsidian eval code="return !!app.plugins.plugins['dataview']" 2>/dev/null
```

### Example queries

```bash
# Find notes by tag (multi-tag, thematic)
obsidian eval code="
  const dv = app.plugins.plugins['dataview'].api;
  const pages = dv.pages('#negotiation OR #communication OR #leadership')
    .sort(p => p.file.mtime, 'desc').slice(0, 10);
  return JSON.stringify(pages.map(p => ({path: p.file.path, tags: p.tags})));
" 2>/dev/null

# Find notes modified in the last 30 days
obsidian eval code="
  const dv = app.plugins.plugins['dataview'].api;
  const cutoff = dv.date('today') - dv.duration('30d');
  return JSON.stringify(dv.pages().where(p => p.file.mtime > cutoff)
    .sort(p => p.file.mtime, 'desc').slice(0, 20)
    .map(p => p.file.path));
" 2>/dev/null

# Find notes missing a required frontmatter field
obsidian eval code="
  const dv = app.plugins.plugins['dataview'].api;
  return JSON.stringify(dv.pages('\"REFERENCES\"')
    .where(p => !p.tags || p.tags.length === 0)
    .map(p => p.file.path));
" 2>/dev/null

# Find notes that link to a given note (backlink graph)
obsidian eval code="
  const file = app.vault.getAbstractFileByPath('BOOKS/Never Split the Difference.md');
  const cache = app.metadataCache.resolvedLinks;
  const backlinks = Object.entries(cache)
    .filter(([src, links]) => Object.keys(links).includes(file?.path))
    .map(([src]) => src);
  return JSON.stringify(backlinks);
" 2>/dev/null
```

### Thematic connection finder (primary use case)

Use this pattern in any skill that needs to find vault connections for a new note:

```bash
# Given a set of theme keywords (e.g. from a book or video topic):
THEMES="negotiation empathy assertive conflict listening"

obsidian eval code="
  const dv = app.plugins.plugins['dataview'].api;
  const themes = ['negotiation', 'empathy', 'assertive', 'conflict', 'listening'];
  const results = dv.pages().where(p => {
    const text = [p.file.name, ...(p.tags || [])].join(' ').toLowerCase();
    return themes.some(t => text.includes(t));
  }).sort(p => p.file.mtime, 'desc').slice(0, 15);
  return JSON.stringify(results.map(p => ({path: p.file.path, tags: p.tags || []})));
" 2>/dev/null || \
grep -rl "negotiation\|empathy\|assertive\|conflict\|listening" "<vault_root>" \
  --include="*.md" --exclude-dir=SCRIPTS 2>/dev/null | head -15
```

---

## Capability 3: Vault Health Report

Run `/vault-health` to get a full health report on the vault's link graph,
orphaned notes, broken links, and tag inventory.

### Run vault health check

```bash
echo "## Vault Health Report"
echo ""

echo "### Orphaned Notes (no backlinks)"
obsidian orphans format=json 2>/dev/null \
  | python3 -c "import sys,json; paths=json.load(sys.stdin); print(f'Total: {len(paths)}'); [print(f'  - {p}') for p in paths[:20]]"

echo ""
echo "### Dead-End Notes (no outgoing links)"
obsidian deadends format=json 2>/dev/null \
  | python3 -c "import sys,json; paths=json.load(sys.stdin); print(f'Total: {len(paths)}'); [print(f'  - {p}') for p in paths[:20]]"

echo ""
echo "### Unresolved Links (broken wikilinks)"
obsidian unresolved format=json 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); print(f'Total: {len(data)}'); [print(f'  - {d}') for d in data[:20]]"

echo ""
echo "### Tag Inventory (top 20 by usage)"
obsidian tags counts sort=count format=json 2>/dev/null \
  | python3 -c "
import sys, json
tags = json.load(sys.stdin)
for t in sorted(tags, key=lambda x: x.get('count',0), reverse=True)[:20]:
    print(f'  {t[\"count\"]:4d}  {t[\"tag\"]}')
"
```

### Task dashboard

Find all open tasks across the vault or within a folder:

```bash
# All open tasks
obsidian tasks todo verbose=true format=json 2>/dev/null \
  | python3 -c "
import sys, json
tasks = json.load(sys.stdin)
for t in tasks:
    print(f'  [ ] {t[\"text\"]}  ({t[\"file\"]}:{t[\"line\"]})')
"

# Open tasks from meeting notes only
obsidian tasks todo verbose=true format=json 2>/dev/null \
  | python3 -c "
import sys, json
tasks = [t for t in json.load(sys.stdin) if 'MEETINGS' in t.get('file','')]
print(f'Open meeting tasks: {len(tasks)}')
for t in tasks:
    print(f'  [ ] {t[\"text\"]}  ({t[\"file\"]})')
"
```

---

## Capability 4: Note Creation from Templates

Use the Obsidian CLI to create notes from vault templates with Templater variables
properly resolved:

```bash
# List available templates
obsidian templates

# Read a template with all variables resolved
obsidian template:read file="TEMPLATES/Meeting Template.md" resolve=true

# Create a new note from a template (read resolved, then write)
CONTENT=$(obsidian template:read file="TEMPLATES/Meeting Template.md" resolve=true)
obsidian create file="MEETINGS/2026/03-March/2026-03-23-New Meeting.md" \
  content="$CONTENT" open=true
```

---

## Workflow: Find Vault Connections for a New Note

This is the recommended pattern for any skill that needs to surface related vault
notes (e.g., youtube-summary, book notes):

```bash
# 1. Try Dataview first (richest results)
DATAVIEW_AVAILABLE=$(obsidian eval code="return !!app.plugins.plugins['dataview']" 2>/dev/null)

if [ "$DATAVIEW_AVAILABLE" = "true" ]; then
  # Use Dataview API with thematic keywords
  obsidian eval code="
    const dv = app.plugins.plugins['dataview'].api;
    const themes = [<comma-separated-theme-keywords>];
    return JSON.stringify(dv.pages().where(p => {
      const text = [p.file.name, ...(p.tags||[])].join(' ').toLowerCase();
      return themes.some(t => text.includes(t));
    }).sort(p => p.file.mtime, 'desc').slice(0,15).map(p => p.file.path));
  " 2>/dev/null

elif obsidian version &>/dev/null; then
  # Fall back to obsidian search
  obsidian search query="<theme_keywords>" format=json limit=20 2>/dev/null

else
  # Final fallback: grep
  grep -rl "<keyword>" "<vault_root>/BOOKS" "<vault_root>/PROJECTS" \
    "<vault_root>/REFERENCES" --include="*.md" 2>/dev/null | head -20
fi
```

---

## Error Handling

- **`obsidian: command not found`**: CLI not on PATH. See obsidian-cli skill prerequisites.
- **`No vault found`**: Run from within vault directory or add `vault=VaultName`.
- **`Obsidian is not running`**: Start Obsidian first; fall back to grep-based search.
- **Dataview not available**: The `obsidian eval` Dataview queries will fail silently; the `|| grep ...` fallback handles this.
