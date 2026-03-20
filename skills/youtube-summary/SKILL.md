---
name: youtube-summary
description: Summarize YouTube videos - fetches transcript, generates summary with key takeaways, and saves to Obsidian vault REFERENCES/ folder
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
---

# YouTube Summary

Fetches a YouTube video transcript, generates a concise summary with key takeaways and tag suggestions, and saves a structured note to the Obsidian vault's `REFERENCES/` folder with the raw transcript in `ATTACHMENTS/`.

## When to Use

Invoke `/youtube-summary <url>` when you want to:
- Capture a YouTube video as a reference note in your vault
- Get a concise summary and key takeaways without watching the full video
- Build a searchable library of video content

## Prerequisites

Install required Python packages:

```bash
pip install youtube-transcript-api yt-dlp
```

Or use `uv` (no pre-install needed):

```bash
uv run --with youtube-transcript-api --with yt-dlp skills/youtube-summary/fetch_youtube.py ...
```

## Workflow

### 1. Discover Vault Root

Read Obsidian configuration to find the active vault:

```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Parse the JSON to find the vault with `"open": true`, or use the most recently opened vault. Extract `vault_root` from the `path` field.

### 2. Fetch Transcript and Metadata

Run the fetch script:

```bash
python3 skills/youtube-summary/fetch_youtube.py "<vault_root>" "<youtube_url>"
```

The script outputs JSON to stdout:

```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Video Title",
  "channel": "Channel Name",
  "duration": "3:32",
  "published": "2009-10-25",
  "note_path": "/vault/REFERENCES/2009/10-October/2009-10-25 - Video Title.md",
  "transcript_path": "/vault/ATTACHMENTS/2009/10-October/2009-10-25 - Video Title - transcript.md",
  "already_exists": false,
  "transcript_length": 4821
}
```

### 3. Handle Already-Existing Notes

If `already_exists` is `true`:
- Read the existing note at `note_path` and display it to the user
- Ask: "This video has already been summarized. Do you want to regenerate the summary?"
- If no, stop here
- If yes, continue with steps 4-7

### 4. Read Transcript

Read the transcript file from the path returned in `transcript_path`.

### 5. Generate Summary, Takeaways, and Tags

First, scan existing tags in REFERENCES/ for consistency:

```bash
grep -rh "^  - " "<vault_root>/REFERENCES/" | sort -u
```

Then generate:
- **Summary**: A concise paragraph (3-6 sentences) capturing the core message
- **Key Takeaways**: 5-8 bullet points with the most actionable or insightful points
- **Tags**: 2-4 tags relevant to the content (use existing tags where possible, suggest new ones if needed; always include `References` and `YouTube`)

### 6. Write Summary JSON

Write the LLM-generated content to a temp file:

```json
{
  "summary": "Concise summary paragraph...",
  "takeaways": [
    "First key insight or action",
    "Second key insight"
  ],
  "tags": ["References", "YouTube", "Leadership", "Engineering"]
}
```

Write to `/tmp/yt_summary_<video_id>.json`.

### 7. Save Summary to Note

Run the script in save-summary mode:

```bash
python3 skills/youtube-summary/fetch_youtube.py "<vault_root>" "<youtube_url>" --save-summary /tmp/yt_summary_<video_id>.json
```

The script injects the summary, takeaways, and tags into the note's frontmatter and body sections.

### 8. Confirm to User

Report completion:
- Note location (relative to vault root)
- Transcript location (relative to vault root)
- First few lines of the generated summary

## Note Format

**Frontmatter**:
```yaml
---
title: "Video Title"
channel: "Channel Name"
url: https://youtube.com/watch?v=xxx
published: YYYY-MM-DD
duration: "MM:SS"
tags:
  - References
  - YouTube
  - <suggested-tags>
created: YYYY-MM-DD HH:MM
transcript: "[[YYYY-MM-DD - Video Title - transcript]]"
---
```

**Body**:
```markdown
# Summary

<generated paragraph>

# Key Takeaways

- <bullet 1>
- <bullet 2>
```

## Directory Structure

```
REFERENCES/
  └── YYYY/
      └── MM-Month/
          └── YYYY-MM-DD - Title.md

ATTACHMENTS/
  └── YYYY/
      └── MM-Month/
          └── YYYY-MM-DD - Title - transcript.md
```

## Error Handling

- **No transcript available**: Inform the user; the note is still created with metadata but no transcript or summary
- **Private/unavailable video**: Report the error from yt-dlp
- **Special characters in title**: Automatically sanitized (same as meeting notes)
- **Network errors**: Report clearly and exit without creating partial files

## Related Skills

- **obsidian-vault-discovery**: Used to discover vault configuration
- **obsidian-vault-setup**: Creates REFERENCES/ and ATTACHMENTS/ directory structure
- **daily-planner**: Similar pattern for creating structured notes from external data
