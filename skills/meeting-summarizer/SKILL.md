---
name: meeting-summarizer
description: Summarize a meeting from transcript artifacts and inject "## Notes by Claude" into an Obsidian meeting file. Also supports extracting Plaud AI summaries and transcripts from share URLs or recording IDs and injecting them as "## Notes by Plaud".
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

Also supports extracting Plaud AI summaries and transcripts directly from Plaud
share URLs or the official Plaud CLI and injecting them as "## Notes by Plaud".

## When to Use

Invoke `/meeting-summarizer` when you want to:
- Summarize a recorded meeting from a transcript file
- Add or refresh AI-generated notes in an existing meeting Obsidian file
- Replace a `## Notes by Gemini` section with `## Notes by Claude`
- Extract a Plaud AI summary from a share URL and inject it as `## Notes by Plaud`
- Save a Plaud transcript to the vault's `TRANSCRIPTS/` folder

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

### Step 2.5 — Detect Plaud Source

After identifying the meeting file, check if a Plaud source is available:

1. Read the meeting frontmatter — check for a `plaud:` field containing a share URL.
2. Ask the user: "Do you have a Plaud share URL, recording ID, or webarchive file to import?"
   - If yes → go to **Step 3a (Plaud extraction)** before Step 3
   - If no → continue to Step 3 (standard transcript workflow)

### Step 3a — Extract from Plaud (if Plaud source provided)

Three methods, in priority order:

#### Method A — Official Plaud CLI (preferred when authenticated)

Check if the CLI is available and authenticated:

```bash
which plaud || npx @plaud-ai/cli --help 2>/dev/null | head -3
cat ~/.plaud/tokens.json 2>/dev/null | head -1
```

If authenticated, use the CLI:

```bash
# Find the recording if you have a name/date but not an ID
plaud search "recording title" --from YYYY-MM-DD
# or
plaud today    # recordings from today

# Download transcript and summary
plaud transcript <file_id> -o /tmp/plaud-transcript.txt
plaud summary <file_id> -o /tmp/plaud-summary.md
```

If not yet authenticated, inform the user:
> Run `! plaud login` (or `! npx @plaud-ai/cli login`) to authenticate, then re-invoke.

Install if needed: `npm install -g @plaud-ai/cli`

#### Method B — Playwright from share URL (no auth required)

Use when the user provides a Plaud share URL (format: `https://web.plaud.ai/s/pub_XXXX` or `https://web.plaud.ai/s/pub_XXXX::TOKEN`). Playwright is pre-installed on this system.

```bash
uv run --with playwright python3 - << 'PYEOF'
from playwright.sync_api import sync_playwright
import sys, json

raw_url = sys.argv[1]
# Normalize: strip ::token suffix, convert /s/ to /nshare/
url = raw_url.split("::")[0].replace("/s/", "/nshare/")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)
    frame = page.frames[0]

    # Summary tab loads by default
    summary_text = frame.evaluate("document.body.innerText")

    # Click Transcript tab
    frame.locator("text=Transcript").first.click()
    page.wait_for_timeout(2000)
    transcript_text = frame.evaluate("document.body.innerText")

    print(json.dumps({"summary": summary_text, "transcript": transcript_text}))
    browser.close()
PYEOF "<share_url>"
```

Parse the JSON output to get `summary` and `transcript` strings. The summary
contains numbered sections with **Conclusion**, **Plan**, and **Discussion Points**.
The transcript contains timestamped, speaker-labeled segments.

#### Method C — Webarchive file

If the user saved the Plaud page as a `.webarchive` file:

```bash
textutil -convert txt -stdout "path/to/file.webarchive"
```

The output contains whichever tab was rendered at save time (usually Summary loads first,
but may only show transcript if the page wasn't fully loaded). Parse to identify which
content is present.

### Step 3b — Parse and Save Plaud Content

After extraction (any method):

**Save Plaud transcript** to `TRANSCRIPTS/`:

- File path: `<vault_root>/TRANSCRIPTS/YYYY-MM-DD - <title> - transcript.md`
- Format: timestamped speaker-labeled markdown (see Output Format Reference below)
- Add a frontmatter note if Plaud speaker labels are known to be incorrect

**Add `plaud:` and `transcript:` to meeting frontmatter** if not already present.

**Generate `## Notes by Plaud`** from the extracted summary (see format below).
Inject into meeting file (Step 8 rules apply).

### Step 3 — Collect Artifacts

Ask the user to provide (or confirm auto-discovered paths):

- **Transcript** (`.vtt`, `.txt`, `.srt`) — required for "Notes by Claude"
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

Read the current meeting file content and inject the generated section(s):

**Ordering** (top to bottom in meeting file):
1. `## Actions`
2. `## Notes by Plaud` (if generated)
3. `## Notes by Claude` (if generated)
4. `## Agenda`
5. `## Recent Meetings`

**Replacement rules:**
- If `## Notes by Plaud` already exists → replace it (idempotent re-runs)
- If `## Notes by Claude` already exists → replace it
- If `## Notes by Gemini` exists → replace it with `## Notes by Claude`
- If inserting for the first time → place according to the ordering above

Use the Edit tool to make the change.

### Step 9 — Confirm to User

Report:
- Meeting file updated (path relative to vault root)
- Transcript file created (if new)
- Speaker mapping used (for Notes by Claude)
- First few lines of the generated summary/notes

## Output Format Reference

### ## Notes by Plaud format

Preserves Plaud's numbered topic structure with Conclusion, Plan, and Discussion Points:

```markdown
## Notes by Plaud

### 1. Topic Title

**Conclusion:** One-sentence outcome for this topic.

**Plan:**
- Action item description — Owner

**Discussion Points:**
- [Speaker Name]: Key point made
- [Speaker Name]: Another point
```

### ## Notes by Claude format

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

### Transcript file format

```markdown
---
source: <plaud_share_url>
date: YYYY-MM-DD
duration: Xm Ys
speakers:
  - Speaker One
  - Speaker Two
---

> [!note] Speaker Note
> Optional note if Plaud auto-labeled speakers incorrectly.

---

**00:00:55 Speaker Name**
Transcript text for this segment.

**00:01:26 Speaker Name**
Transcript text continues...
```

## Related Skills

- **obsidian-vault-discovery**: Used to discover vault configuration
- **meeting-planner**: Creates and hydrates meeting files from Google Calendar
- **daily-planner**: Orchestrates daily note creation and meeting file hydration
