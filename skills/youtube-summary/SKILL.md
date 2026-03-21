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
  "transcript_path": "/vault/ATTACHMENTS/Transcripts/2009-10-25 - Video Title - transcript.md",
  "timestamped_transcript_path": "/vault/ATTACHMENTS/Transcripts/2009-10-25 - Video Title - transcript-timestamped.md",
  "heatmap_path": "/vault/ATTACHMENTS/Transcripts/2009-10-25 - Video Title - heatmap.md",
  "already_exists": false,
  "transcript_length": 4821
}
```

`timestamped_transcript_path` will be `null` if no transcript is available. `heatmap_path` will be `null` if the video has no YouTube heatmap data (requires sufficient view count).

### 3. Handle Already-Existing Notes

If `already_exists` is `true`:
- Read the existing note at `note_path` and display it to the user
- Ask: "This video has already been summarized. Do you want to regenerate the summary?"
- If no, stop here
- If yes, continue with steps 4-7

### 4. Read Transcript and Supporting Data

Read the transcript file from `transcript_path`.

If `timestamped_transcript_path` is non-null, also read it — this file contains each transcript line prefixed with `[MM:SS]` timestamps. Use it when generating timestamp-annotated Key Takeaways and Protocols.

If `heatmap_path` is non-null, read it — this file lists the top most-replayed segments with their time ranges and intensity scores. Use it to populate the Most Replayed section.

### 5. Discover Vault Context

Scan the vault for notes related to the video's topics. Use 3-5 targeted keyword searches based on people names, project names, and key concepts from the transcript. Exclude script folders:

```bash
# Search for people or topic keywords across the vault
grep -rl "<keyword>" "<vault_root>" \
  --include="*.md" \
  --exclude-dir=SCRIPTS --exclude-dir=DATAVIEW_SCRIPTS \
  2>/dev/null | head -20
```

Note any matching notes — they will be referenced in the Vault Connections section.

### 6. Generate All Sections, Tags, and Vault Connections

First, scan existing tags in REFERENCES/ for consistency:

```bash
grep -rh "^  - " "<vault_root>/REFERENCES/" | sort -u
```

Then generate all sections:

- **TLDR**: 2-3 sentences capturing the video's core point. Should stand alone as a complete answer to "what is this video about?"

- **Summary**: 3-5 paragraphs covering the main points, arguments, supporting evidence, and conclusions. More depth than TLDR but still curated.

- **Key Takeaways**: 5-10 bullet points, each prefixed with a `[MM:SS]` timestamp from the timestamped transcript indicating where that insight appears. Format: `- [3:15] The key insight...`. Focus on actionable or insightful points.

- **Protocols & Instructions**: If the video contains actionable steps, dosages, specific recommendations, or how-to sequences, extract them as numbered or bulleted steps with `[MM:SS]` timestamps. If the video contains no protocols, write: `None mentioned.`

- **Most Replayed**: If `heatmap_path` is non-null, read the heatmap file and cross-reference each peak timestamp with the timestamped transcript to describe what is happening at that moment. Format: `- [MM:SS] Brief description of what the video covers at this moment (intensity: X.XX)`. List the top 5-10 peaks sorted by time. If `heatmap_path` is null, write: `Heatmap data not available for this video.`

- **Tags**: 2-4 tags relevant to the content. Use existing vault tags where possible; suggest new ones if needed. Always include `References` and `YouTube`.

- **Vault Connections**: 2-5 bullet points connecting video themes to existing vault notes found in Step 5, using `[[wiki-links]]`. Omit if no meaningful connections are found.

- **Recommendations**: 2-5 bullet points suggesting future actions prompted by this video. Mix from: related topics to research, books/papers/videos to explore, notes to create or revisit in the vault, decisions to consider, or personal reflections. Be specific (e.g., `- Read *Thinking, Fast and Slow* by Kahneman for deeper context on cognitive biases`).

### 7. Write Summary JSON

Write the LLM-generated content to a temp file:

```json
{
  "tldr": "2-3 sentence ultra-brief summary of the video's core point.",
  "summary": "Multi-paragraph overview covering main points and conclusions...",
  "takeaways": [
    "[1:23] First key insight with timestamp",
    "[5:10] Second key insight with timestamp"
  ],
  "protocols": "- [2:00] Step 1: Do this\n- [3:30] Step 2: Then this\n\nOr: None mentioned.",
  "most_replayed": "- [4:15] Most-watched moment: presenter reveals key finding (intensity: 0.92)\n- [8:30] Peak engagement: live demonstration (intensity: 0.87)\n\nOr: Heatmap data not available for this video.",
  "tags": ["References", "YouTube", "Leadership", "Engineering"],
  "vault_connections": "- Relates to [[Person Name]] discussed in [[Meeting Note]]\n- Connects to themes in [[Reference Note]]",
  "recommendations": "- Research [[Topic]] further in the vault\n- Read *Book Title* by Author for deeper context\n- Create a note on [[Concept]] to capture learnings"
}
```

Write to `/tmp/yt_summary_<video_id>.json`.

Notes:
- `vault_connections` may be omitted if no meaningful connections were found
- All values are markdown strings (for `protocols`, `most_replayed`, `vault_connections`, `recommendations`) or arrays of strings (for `takeaways`, `tags`)

### 8. Save Summary to Note

Run the script in save-summary mode:

```bash
python3 skills/youtube-summary/fetch_youtube.py "<vault_root>" "<youtube_url>" --save-summary /tmp/yt_summary_<video_id>.json
```

The script injects the summary, takeaways, tags, and vault connections into the note's frontmatter and body sections.

### 9. Confirm to User

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
# TLDR

<2-3 sentence ultra-brief summary>

# Summary

<3-5 paragraph overview>

# Key Takeaways

- [1:23] First key insight with timestamp
- [5:10] Second key insight with timestamp

# Protocols & Instructions

- [2:00] Step 1: Do this
- [3:30] Step 2: Then this

(Or: None mentioned.)

# Most Replayed

- [4:15] Most-watched moment: presenter reveals key finding (intensity: 0.92)
- [8:30] Peak engagement: live demonstration (intensity: 0.87)

(Or: Heatmap data not available for this video.)

# Vault Connections

- Relates to [[Person Name]] discussed in [[Meeting Note]]
- Connects to themes in [[Reference Note]]

# Recommendations

- Research [[Topic]] further
- Read *Book Title* by Author
- Create a note on [[Concept]]
```

## Directory Structure

```
REFERENCES/
  └── YYYY/
      └── MM-Month/
          └── YYYY-MM-DD - Title.md

ATTACHMENTS/
  └── Transcripts/
      ├── YYYY-MM-DD - Title - transcript.md           (plain text, readable)
      ├── YYYY-MM-DD - Title - transcript-timestamped.md  (LLM input, [MM:SS] prefixed)
      └── YYYY-MM-DD - Title - heatmap.md              (LLM input, peak segments; omitted if no heatmap)
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
