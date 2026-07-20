---
name: article-summarizer
description: Summarize web articles - fetches content, generates summary with key takeaways, and saves to Obsidian vault REFERENCES/ folder
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - Edit
  - WebFetch
---

# Article Summary

Fetches a web article, generates a concise summary with key takeaways and vault connections, and saves a structured reference note to the Obsidian vault's `REFERENCES/` folder.

## When to Use

Invoke `/article-summarizer <url>` when you want to:
- Capture a web article as a reference note in your vault
- Get a concise summary and key takeaways without reading the full piece
- Connect an article's ideas to existing notes in your vault
- Build a searchable library of articles and their learnings

## Workflow

### 1. Discover Vault Root

Read Obsidian configuration to find the active vault:

```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Parse the JSON to find the vault with `"open": true`, or use the most recently opened vault. Extract `vault_root` from the `path` field.

### 2. Fetch Article Content and Metadata

Fetch the article in two passes using WebFetch:

**Pass A — metadata extraction** (structured JSON):

```
WebFetch(url, prompt="Extract from this article: 1) the article title, 2) author name(s) — omit if not found, 3) publication date in YYYY-MM-DD format — omit if not found, 4) the site or publication name (e.g. 'The Atlantic', 'hbr.org'). Return ONLY a JSON object with keys: title, author, published, source. Use empty string for any field not found.")
```

**Pass B — full article content** (clean markdown):

```
WebFetch(url, prompt="Extract the main article content as clean markdown. Remove navigation menus, ads, sidebars, footers, cookie notices, social sharing buttons, related articles widgets, and comments sections. Preserve the article's headings, body paragraphs, lists, blockquotes, code blocks, and emphasis. Return only the article content.")
```

Then run the Python script to create the skeleton note:

```bash
python3 skills/article-summarizer/fetch_article.py "<vault_root>" "<url>" \
  --title "<title>" \
  --author "<author>" \
  --published "<published>" \
  --source "<source>"
```

The script outputs JSON to stdout:

```json
{
  "title": "Article Title",
  "author": "Author Name",
  "published": "2026-07-15",
  "source": "example.com",
  "url": "https://example.com/article",
  "note_path": "/vault/REFERENCES/2026/07-July/2026-07-20 - Article Title.md",
  "already_exists": false
}
```

Note: `note_path` uses **today's date** (when the article was consumed), not the publication date.

**Error handling**: If WebFetch returns very little content (under 200 words) or an error page, inform the user. Suggest they paste the article text directly as a follow-up message. Proceed with skeleton creation using whatever metadata was extracted.

### 3. Handle Already-Existing Notes

If `already_exists` is `true`:
- Read the existing note at `note_path` and display it to the user
- Ask: "This article has already been summarized. Do you want to regenerate the summary?"
- If no, stop here
- If yes, continue with steps 4–7

### 4. Article Content in Context

The WebFetch content from Step 2 Pass B is already in your context window. Use it for summarization in Step 6. No additional file reads are needed (unlike the youtube-summarizer which reads transcript files).

### 5. Discover Vault Context

Scan the vault for notes related to the article's topics using the best available method. Use thematic keywords, not just proper nouns — e.g. for an article about remote work search "async", "distributed teams", "productivity", not just company names.

#### Preferred: Obsidian indexed search (when CLI is available)

```bash
# Check if obsidian CLI is available
obsidian version &>/dev/null && CLI_AVAILABLE=true || CLI_AVAILABLE=false

# Option A: Dataview API (richest results — tags, aliases, backlinks)
obsidian eval code="
  const dv = app.plugins.plugins['dataview']?.api;
  if (!dv) return '[]';
  const themes = [<comma-separated-theme-keywords>];
  return JSON.stringify(dv.pages().where(p => {
    const text = [p.file.name, ...(p.tags||[])].join(' ').toLowerCase();
    return themes.some(t => text.includes(t));
  }).sort(p => p.file.mtime, 'desc').slice(0,15).map(p => p.file.path));
" 2>/dev/null

# Option B: Obsidian indexed search (fallback if Dataview unavailable)
obsidian search query="<theme_keywords>" format=json limit=20 2>/dev/null \
  | python3 -c "import sys,json; [print(r.get('path','')) for r in json.load(sys.stdin)]"
```

#### Fallback: grep (when Obsidian is not running)

```bash
# Search BOOKS/ and PROJECTS/ first — most likely to have thematic connections
grep -rl "<keyword>" "<vault_root>/BOOKS" "<vault_root>/PROJECTS" \
  --include="*.md" 2>/dev/null | head -15

# Broader vault search
grep -rl "<keyword>" "<vault_root>" \
  --include="*.md" \
  --exclude-dir=SCRIPTS --exclude-dir=DATAVIEW_SCRIPTS \
  2>/dev/null | head -20
```

Note any matching notes — they will be referenced in the Vault Connections section. Prioritize connections to BOOKS/ (personal reading notes), PROJECTS/ (ongoing work), and existing REFERENCES/ notes over daily notes.

### 6. Generate All Sections, Tags, and Vault Connections

First, scan existing tags in REFERENCES/ for consistency:

```bash
grep -rh "^  - " "<vault_root>/REFERENCES/" | sort -u
```

Then generate all sections using the article content from Step 2:

- **TLDR**: 1-5 sentences capturing the article's core argument, thesis, or key finding. Should stand alone as a complete answer to "what is this article about and why does it matter?"

- **Summary**: 3-5 paragraphs covering the main points, arguments, supporting evidence, and conclusions. More depth than TLDR but still curated. Capture the author's reasoning, not just their conclusions.

- **Key Takeaways**: 5-10 bullet points of actionable insights, surprising findings, or memorable ideas from the article. Focus on what is useful, applicable, or worth remembering. No timestamps (unlike YouTube).

- **Tags**: 2-4 tags relevant to the content. Use existing vault tags where possible; suggest new ones if needed. Always include `References` and `Articles`.

- **Vault Connections**: 2-5 bullet points connecting the article's themes to existing vault notes found in Step 5, using `[[wiki-links]]`. Explain *why* the connection is relevant — not just a link, but a sentence. Omit this section if no meaningful connections are found.

- **Recommendations**: 2-5 bullet points suggesting follow-up actions prompted by this article. Mix from: related topics to research, books/papers/articles to read, notes to create or revisit in the vault, decisions to consider, or ways to apply the learnings. Be specific (e.g., `- Read *Deep Work* by Cal Newport for complementary perspective on focused attention`).

### 7. Write Summary JSON

Write the LLM-generated content to a temp file:

```json
{
  "tldr": "1-5 sentence summary of the article's core argument and why it matters.",
  "summary": "Multi-paragraph overview covering main points, reasoning, and conclusions...",
  "takeaways": [
    "First actionable insight or memorable finding",
    "Second key point worth remembering"
  ],
  "tags": ["References", "Articles", "Leadership", "Strategy"],
  "vault_connections": "- Connects to [[Note Name]] — both explore the tension between X and Y\n- Relates to [[Other Note]] — the author's approach mirrors what was discussed in that meeting",
  "recommendations": "- Read *Book Title* by Author for deeper context on this topic\n- Explore [[Concept]] in the vault — this article reframes it\n- Create a note on [[New Idea]] to capture the key framework"
}
```

Determine the URL hash:

```bash
python3 -c "import hashlib,urllib.parse; url='<url>'; \
  parsed=urllib.parse.urlparse(url); \
  norm=urllib.parse.urlunparse(('https',parsed.netloc,parsed.path,parsed.params,'',''));\
  print(hashlib.sha256(norm.encode()).hexdigest()[:8])"
```

Write to `$HOME/.cache/obsidian-claude-plugins/article_summary_<hash>.json` (create the directory with `mkdir -p "$HOME/.cache/obsidian-claude-plugins"` if needed).

Notes:
- `vault_connections` and `recommendations` are markdown strings
- `takeaways` and `tags` are arrays of strings
- Omit `vault_connections` key entirely if no meaningful connections were found

### 8. Save Summary to Note

Run the script in save-summary mode:

```bash
CACHE_DIR="$HOME/.cache/obsidian-claude-plugins"
mkdir -p "$CACHE_DIR"
HASH=$(python3 -c "import hashlib,urllib.parse; url='<url>'; parsed=urllib.parse.urlparse(url); norm=urllib.parse.urlunparse(('https',parsed.netloc,parsed.path,parsed.params,'','')); print(hashlib.sha256(norm.encode()).hexdigest()[:8])")
python3 skills/article-summarizer/fetch_article.py "<vault_root>" "<url>" \
  --save-summary "$CACHE_DIR/article_summary_$HASH.json"
```

The script injects the generated content into the note's frontmatter (tags) and body sections (TLDR, Summary, Key Takeaways, Vault Connections, Recommendations).

### 9. Add Daily Note Breadcrumb

Add a breadcrumb to today's daily note so there's a record of the article being read and summarized.

**Construct today's daily note path** using `vault_root` and today's date:

```
<vault_root>/DAILY_NOTES/YYYY/MM-Month/YYYY-MM-DD DayOfWeek.md
```

For example: `DAILY_NOTES/2026/07-July/2026-07-20 Monday.md`

**If the daily note does not exist**, create it:

```bash
mkdir -p "<vault_root>/DAILY_NOTES/YYYY/MM-Month"
```

Then use the Write tool to create the file with these sections in order:

1. YAML frontmatter with `created: YYYY-MM-DD HH:MM` (today's timestamp) and `tags: [Daily_Notes]`
2. `# 📓 Journal` with `## Morning thoughts` and `## Evening reflection` subsections
3. `# ✅ Tasks` with `## Today`, `## This week`, and `## No due date` subsections (each containing a fenced `tasks` Dataview query block)
4. `# 📅 Meetings` (empty body)
5. `# 📝 Notes` (empty body)

This matches the structure of `TEMPLATES/Daily Note Template.md` in the vault.

**Check for duplicates** before appending — search the daily note content for the reference note's filename stem (everything after the last `/` in `note_path`, without the `.md` extension). If found, skip the append.

**Append the breadcrumb** using the Edit tool to add it to the `# 📝 Notes` section. Since Notes is the last section, append to the end of the file:

```
- 📰 [[RELATIVE_NOTE_PATH|TITLE]] — AUTHOR, SOURCE (PUBLISHED)
```

Where:
- `RELATIVE_NOTE_PATH` is `note_path` with the `vault_root/` prefix stripped (e.g., `REFERENCES/2026/07-July/2026-07-20 - Article Title`)
- `TITLE`, `AUTHOR`, `SOURCE` come from the Step 2 JSON output
- `PUBLISHED` is the article's publication date (if known); omit the parenthetical if unknown

### 10. Confirm to User

Report completion:
- Note location (relative to vault root)
- First few lines of the generated TLDR and Key Takeaways
- Vault connections found (count and titles)
- Daily note breadcrumb: added (or "already present" if duplicate was detected)

## Note Format

**Frontmatter**:
```yaml
---
title: "Article Title"
author: "Author Name"
url: https://example.com/article
published: 2026-07-15
source: "example.com"
tags:
  - References
  - Articles
  - <suggested-tags>
created: 2026-07-20 11:30
---
```

**Body**:
```markdown
## TLDR

<1-5 sentence answer to "what is this article about and why does it matter?">

## Summary

<3-5 paragraph overview of main points, reasoning, and conclusions>

## Key Takeaways

- First actionable insight or memorable finding
- Second key point worth remembering
- Third insight, surprising finding, or useful framework

## Vault Connections

- Connects to [[Note Name]] — both explore the tension between X and Y
- Relates to themes in [[Reference Note]] — the author's approach mirrors...

## Recommendations

- Read *Book Title* by Author for deeper context
- Explore [[Concept]] in the vault — this article reframes it
- Create a note on [[New Idea]] to capture the key framework
```

## Directory Structure

```
REFERENCES/
  └── YYYY/
      └── MM-Month/
          └── YYYY-MM-DD - Title.md     (today's date, not publish date)
```

## Error Handling

- **Paywalled content**: Inform the user; suggest they paste the article text manually as a follow-up message
- **Blocked by site (403/captcha)**: Report clearly; skeleton note is still created with metadata if extraction worked
- **Missing author/date**: Proceed with empty fields; the note is created and the user can fill them in
- **Large articles**: WebFetch may truncate very long pieces; note in the summary that it is based on the content returned by WebFetch
- **Special characters in title**: Automatically sanitized by the Python script (same as meeting notes)

## Related Skills

- **obsidian-vault-discovery**: Used to discover vault configuration
- **obsidian-vault-setup**: Creates REFERENCES/ directory structure and templates
- **youtube-summarizer**: Companion skill for video content (same reference note pattern)
- **obsidian-vault-intelligence**: Vault connection search patterns
- **daily-planner**: Similar pattern for daily note breadcrumbs
