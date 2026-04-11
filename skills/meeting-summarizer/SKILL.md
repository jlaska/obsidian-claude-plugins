---
name: meeting-summarizer
description: Summarize a meeting from transcript artifacts and inject "## Notes by Claude" into an Obsidian meeting file
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - Edit
---

# Meeting Summarizer

Reads a meeting transcript (VTT, TXT, SRT) and optional agenda/prep notes, then
generates a structured "## Notes by Claude" summary and injects it into the
corresponding Obsidian meeting file.

## When to Use

Invoke `/meeting-summarizer` when you want to:
- Summarize a recorded meeting from a transcript file
- Add or refresh AI-generated notes in an existing meeting Obsidian file
- Replace a `## Notes by Gemini` section with `## Notes by Claude`

## Workflow

### Step 1 — Discover Vault Root

Read Obsidian configuration to find the active vault:

```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Parse the JSON to find the vault with `"open": true`. Extract `vault_root`.

### Step 2 — Identify the Meeting File

If invoked with an argument (e.g., `/meeting-summarizer 2026-04-09`), use it to
locate the meeting file:

```bash
find "<vault_root>/MEETINGS" -name "<arg>*.md" | head -5
```

If multiple matches or no argument: use AskUserQuestion to let the user select
or provide the meeting file path. Show the matched file and confirm before proceeding.

### Step 3 — Collect Artifacts

Ask the user to provide (or confirm auto-discovered paths):

- **Transcript** (`.vtt`, `.txt`, `.srt`) — required
- **Agenda / prep notes** (`.md`) — optional, used only as framing context
- **Recording** (`.m4a`, `.m4v`) — noted in the summary output but not processed

Auto-discovery: check `~/Projects/coaching/sessions/<YYYY-MM-DD>/` where the date
matches the meeting file date. Show any found artifacts and ask the user to confirm.

### Step 4 — Parse the Transcript

Read the transcript file. Strip VTT formatting if needed:

```bash
# For .vtt files: strip timestamp lines (00:00:00.000 --> 00:00:01.040)
# and sequence numbers, keeping Speaker: text lines only
```

Use this Python snippet (via uv) to parse VTT into clean diarised text:

```bash
uv run --with webvtt-py python3 - << 'EOF'
import webvtt, sys
transcript = []
for caption in webvtt.read(sys.argv[1]):
    text = caption.text.strip()
    if text:
        transcript.append(text)
print('\n'.join(transcript))
EOF "<transcript_path>"
```

If `.txt` format: read directly.

Output: clean text with speaker labels preserved (e.g., `JP: Smart.\nJames L: Continue feeding...`).

### Step 5 — Confirm Speakers

1. Extract unique speaker labels from the transcript (prefix before first `:` on each line).
2. Display the found labels to the user.
3. Use AskUserQuestion: "Map speaker labels to PEOPLE/ notes?"
   - If yes: search `PEOPLE/*.md` for name candidates (check `aliases:` frontmatter)
   - Present candidates per label; user confirms match or selects "keep as-is"
   - On confirmed match: add the VTT label as an alias in the PEOPLE note's frontmatter
   - Build a speaker map: `{"JP": "John Poelstra", "James L": "James Laska"}`
4. Apply the speaker map to the clean transcript text before summarization.
5. Always show the confirmed speaker list and ask for final confirmation before generating.

### Step 6 — Read Agenda (if provided)

Read the agenda/prep notes file. Use this as framing context in the summary prompt
(it helps identify expected topics and decision points) but do NOT quote it directly
in the "## Notes by Claude" output — the summary must be grounded in the transcript.

### Step 7 — Generate "## Notes by Claude"

Generate the summary using the full transcript text (with resolved speaker names)
and optional agenda as context. Follow the intellectronica Task 2 structure:

```markdown
## Notes by Claude

### Summary
<One paragraph: overall meeting purpose, who attended, and primary outcome.>

### Key Discussion Points
#### <Major Topic Heading>
- Specific detail or sub-point
- _"Verbatim quote from transcript"_ — Speaker Name

#### <Next Major Topic>
- ...

### Decisions Made
- Concrete decision reached (or "None recorded" if none)

### Action Items
- **Task description** — Owner — Deadline (if mentioned)
(or "None recorded" if none explicitly stated)

### Key Quotes
- _"Verbatim quote that captures a key moment"_ — Speaker Name
```

**Formatting rules:**
- Use `##` for the section heading, `###` for sub-sections, `####` for topic headings
- Integrate verbatim quotes (italicised, attributed) within Key Discussion Points
- Keep Key Quotes to 3-5 standout moments
- Action items: bold the task, name the owner, include deadline only if explicitly stated
- If a section has nothing to report, write "None recorded."

### Step 8 — Inject into Meeting File

Read the current meeting file content and inject the generated section:

- If `## Notes by Claude` already exists → replace it (idempotent re-runs)
- If `## Notes by Gemini` exists → replace it with `## Notes by Claude`
- Otherwise → append at the end of the file

Use the Edit tool to make the change.

### Step 9 — Confirm to User

Report:
- Meeting file updated (path relative to vault root)
- Speaker mapping used
- First few lines of the generated summary

## Output Format Reference

```markdown
## Notes by Claude

### Summary
James Laska and John Poelstra met for their bi-weekly coaching session, focusing
on [primary topics]. Key outcomes included [decisions/commitments].

### Key Discussion Points
#### Leadership & Org Changes
- Appointed Deepika as lead for a new engineering team
- _"This is you leading. Not grinding, not shoveling."_ — John Poelstra

#### Career & Networking
- LinkedIn outreach still deferred; June remains the soft deadline
- _"You don't need the resume to be amazing. It just has to be good enough."_ — John Poelstra

### Decisions Made
- None recorded.

### Action Items
- **Schedule LinkedIn/resume work block** — James Laska — Thursday April 16, 2-5pm
- **Follow up with Tailscale contacts** — James Laska

### Key Quotes
- _"What's the life you want to live?"_ — John Poelstra
- _"June is still the soft deadline. It's April 9. That's 8 weeks."_ — John Poelstra
```

## Related Skills

- **obsidian-vault-discovery**: Used to discover vault configuration
- **meeting-planner**: Creates and hydrates meeting files from Google Calendar
- **daily-planner**: Orchestrates daily note creation and meeting file hydration
