#!/usr/bin/env python3
"""
Fetch YouTube video transcript and metadata, create Obsidian notes.

Modes:
  Fetch mode (default):
    python3 fetch_youtube.py <vault_root> <youtube_url>
    - Validates URL, extracts video ID
    - Fetches metadata via yt-dlp
    - Fetches transcript via youtube-transcript-api
    - Creates transcript file in ATTACHMENTS/
    - Creates skeleton note in REFERENCES/
    - Outputs JSON summary to stdout

  Save-summary mode:
    python3 fetch_youtube.py <vault_root> <youtube_url> --save-summary <json_path>
    - Reads summary JSON (summary, takeaways, tags)
    - Injects content into existing note
    - Updates frontmatter tags
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats.

    Supported:
      https://www.youtube.com/watch?v=VIDEO_ID
      https://youtu.be/VIDEO_ID
      https://www.youtube.com/shorts/VIDEO_ID
      https://youtube.com/watch?v=VIDEO_ID&list=...
    """
    patterns = [
        r'(?:youtube\.com/watch\?(?:[^&]*&)*v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def validate_youtube_url(url: str) -> str:
    """Validate YouTube URL and return canonical watch URL.

    Returns:
        Canonical URL like https://youtube.com/watch?v=VIDEO_ID

    Raises:
        ValueError: If URL is not a valid YouTube URL
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {url}")
    return f"https://youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def fetch_metadata(video_id: str) -> dict:
    """Fetch video metadata using yt-dlp.

    Uses extract_info(skip_download=True) to get title, channel, duration,
    upload date, and description without downloading any video content.

    Returns dict with keys: title, channel, duration_seconds, published, description, url
    """
    try:
        import yt_dlp
    except ImportError:
        raise ImportError("yt-dlp is required. Install with: pip install yt-dlp")

    url = f"https://youtube.com/watch?v={video_id}"

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get('title', 'Untitled')
    channel = info.get('channel') or info.get('uploader') or ''
    duration_seconds = info.get('duration') or 0
    upload_date = info.get('upload_date', '')  # YYYYMMDD
    description = info.get('description', '')

    # Parse upload date
    published = ''
    if upload_date and len(upload_date) == 8:
        try:
            dt = datetime.strptime(upload_date, '%Y%m%d')
            published = dt.strftime('%Y-%m-%d')
        except ValueError:
            published = upload_date

    return {
        'title': title,
        'channel': channel,
        'duration_seconds': duration_seconds,
        'published': published,
        'description': description,
        'url': url,
    }


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

def fetch_transcript(video_id: str) -> Optional[str]:
    """Fetch transcript using youtube-transcript-api.

    Tries in order:
      1. English manual captions
      2. English auto-generated captions
      3. Any available language

    Returns plain text transcript (no timestamps), or None if unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    except ImportError:
        raise ImportError("youtube-transcript-api is required. Install with: pip install youtube-transcript-api")

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
    except Exception as e:
        print(f"  Warning: Could not list transcripts: {e}", file=sys.stderr)
        return None

    transcript = None

    # Try English manual first
    try:
        transcript = transcript_list.find_manually_created_transcript(['en'])
    except Exception:
        pass

    # Try English auto-generated
    if transcript is None:
        try:
            transcript = transcript_list.find_generated_transcript(['en'])
        except Exception:
            pass

    # Try any language
    if transcript is None:
        try:
            available = list(transcript_list)
            if available:
                transcript = available[0]
        except Exception:
            pass

    if transcript is None:
        return None

    try:
        entries = transcript.fetch()
        # Concatenate text, joining with spaces, normalizing whitespace
        parts = []
        for entry in entries:
            text = entry.text.strip()
            # Remove [Music], [Applause] etc.
            text = re.sub(r'\[[^\]]+\]', '', text).strip()
            if text:
                parts.append(text)
        return ' '.join(parts)
    except Exception as e:
        print(f"  Warning: Could not fetch transcript entries: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# File path utilities
# ---------------------------------------------------------------------------

def sanitize_title(title: str) -> str:
    """Sanitize video title for use as filesystem name."""
    title = title.replace('/', ' - ')
    title = title.replace(':', ' - ')
    title = title.replace('|', ' - ')
    title = re.sub(r'[<>"\\?*]', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title.strip()


def get_date_subfolder(dt: datetime) -> str:
    """Return YYYY/MM-Month folder string."""
    return f"{dt.strftime('%Y')}/{dt.strftime('%m')}-{dt.strftime('%B')}"


def get_note_path(vault_root: Path, safe_title: str, dt: datetime) -> Path:
    """Return full path for REFERENCES note."""
    date_str = dt.strftime('%Y-%m-%d')
    subfolder = get_date_subfolder(dt)
    return vault_root / 'REFERENCES' / subfolder / f"{date_str} - {safe_title}.md"


def get_transcript_path(vault_root: Path, safe_title: str, dt: datetime) -> Path:
    """Return full path for ATTACHMENTS transcript."""
    date_str = dt.strftime('%Y-%m-%d')
    subfolder = get_date_subfolder(dt)
    return vault_root / 'ATTACHMENTS' / subfolder / f"{date_str} - {safe_title} - transcript.md"


def format_duration(seconds: int) -> str:
    """Convert seconds to HH:MM:SS or MM:SS string."""
    if not seconds:
        return '0:00'
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_transcript_file(path: Path, metadata: dict, transcript: str) -> None:
    """Write transcript markdown file to ATTACHMENTS/."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created_dt = datetime.now().strftime('%Y-%m-%d %H:%M')

    title = metadata['title']
    url = metadata['url']

    lines = [
        '---',
        f'title: "{title} - Transcript"',
        f'source: {url}',
        'tags:',
        '  - Attachments',
        '  - Transcript',
        f'created: {created_dt}',
        '---',
        '',
        transcript,
        '',
    ]
    path.write_text('\n'.join(lines))


def write_skeleton_note(path: Path, metadata: dict, transcript_stem: str) -> None:
    """Write skeleton reference note to REFERENCES/ with empty Summary/Takeaways."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created_dt = datetime.now().strftime('%Y-%m-%d %H:%M')

    title = metadata['title']
    channel = metadata['channel']
    url = metadata['url']
    published = metadata['published']
    duration = format_duration(metadata['duration_seconds'])

    lines = [
        '---',
        f'title: "{title}"',
        f'channel: "{channel}"',
        f'url: {url}',
        f'published: {published}',
        f'duration: "{duration}"',
        'tags:',
        '  - References',
        '  - YouTube',
        f'created: {created_dt}',
        f'transcript: "[[{transcript_stem}]]"',
        '---',
        '',
        '# Summary',
        '',
        '',
        '# Key Takeaways',
        '',
        '',
    ]
    path.write_text('\n'.join(lines))


def save_summary(note_path: Path, summary_json: dict) -> None:
    """Inject LLM-generated summary, takeaways, and tags into existing note.

    Updates:
      - frontmatter tags (merges, preserves existing)
      - # Summary section body
      - # Key Takeaways section body

    Preserves any user-added content below the Key Takeaways section.
    """
    if not note_path.exists():
        raise FileNotFoundError(f"Note not found: {note_path}")

    content = note_path.read_text()

    summary_text = summary_json.get('summary', '').strip()
    takeaways = summary_json.get('takeaways', [])
    new_tags = summary_json.get('tags', [])

    # --- Update tags in frontmatter ---
    if new_tags:
        content = _merge_frontmatter_tags(content, new_tags)

    # --- Update # Summary section ---
    if summary_text:
        content = _replace_section(content, '# Summary', summary_text)

    # --- Update # Key Takeaways section ---
    if takeaways:
        bullets = '\n'.join(f'- {t}' for t in takeaways)
        content = _replace_section(content, '# Key Takeaways', bullets)

    note_path.write_text(content)


def _merge_frontmatter_tags(content: str, new_tags: list) -> str:
    """Add new tags to frontmatter tags list without duplicating existing ones."""
    if not content.startswith('---'):
        return content

    end = content.find('---', 3)
    if end == -1:
        return content

    frontmatter = content[3:end]
    after = content[end:]

    # Find existing tags block
    tags_match = re.search(r'^tags:\n((?:  - .+\n)*)', frontmatter, re.MULTILINE)
    if not tags_match:
        return content

    existing_block = tags_match.group(0)
    existing_tags = re.findall(r'^  - (.+)$', existing_block, re.MULTILINE)

    # Merge, preserving order: existing first, then new
    merged = list(existing_tags)
    for tag in new_tags:
        if tag not in merged:
            merged.append(tag)

    new_block = 'tags:\n' + ''.join(f'  - {t}\n' for t in merged)
    new_frontmatter = frontmatter[:tags_match.start()] + new_block + frontmatter[tags_match.end():]
    return '---' + new_frontmatter + after


def _replace_section(content: str, heading: str, new_body: str) -> str:
    """Replace the body of a markdown section identified by `heading`.

    Preserves content in subsequent sections. If heading not found, appends.
    """
    heading_pattern = re.escape(heading)
    # Find the heading
    match = re.search(rf'^{heading_pattern}\s*$', content, re.MULTILINE)
    if not match:
        # Append new section at end
        return content.rstrip() + f'\n\n{heading}\n\n{new_body}\n'

    start = match.end()
    # Find next heading of same or higher level (# or ##)
    level = len(re.match(r'^(#+)', heading).group(1))
    next_heading = re.search(r'^#{1,' + str(level) + r'} ', content[start:], re.MULTILINE)

    if next_heading:
        section_end = start + next_heading.start()
        return content[:start] + f'\n{new_body}\n\n' + content[section_end:]
    else:
        return content[:start] + f'\n{new_body}\n'


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def fetch_mode(vault_root: Path, url: str) -> dict:
    """Fetch transcript and metadata, create files, return JSON summary."""

    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    canonical_url = f"https://youtube.com/watch?v={video_id}"

    print(f"Fetching metadata for video: {video_id}", file=sys.stderr)
    metadata = fetch_metadata(video_id)
    metadata['url'] = canonical_url

    safe_title = sanitize_title(metadata['title'])

    # Determine dates for file paths
    published_str = metadata.get('published', '')
    if published_str:
        try:
            file_dt = datetime.strptime(published_str, '%Y-%m-%d')
        except ValueError:
            file_dt = datetime.now()
    else:
        file_dt = datetime.now()

    note_path = get_note_path(vault_root, safe_title, file_dt)
    transcript_path = get_transcript_path(vault_root, safe_title, file_dt)

    # Check idempotency
    if note_path.exists():
        print(f"Note already exists: {note_path}", file=sys.stderr)
        result = {
            'video_id': video_id,
            'title': metadata['title'],
            'channel': metadata['channel'],
            'duration': format_duration(metadata['duration_seconds']),
            'published': metadata['published'],
            'note_path': str(note_path),
            'transcript_path': str(transcript_path),
            'already_exists': True,
            'transcript_length': 0,
        }
        if transcript_path.exists():
            result['transcript_length'] = len(transcript_path.read_text())
        return result

    # Fetch transcript
    print(f"Fetching transcript...", file=sys.stderr)
    transcript = fetch_transcript(video_id)

    transcript_length = 0
    if transcript:
        transcript_length = len(transcript)
        print(f"Writing transcript ({transcript_length} chars)...", file=sys.stderr)
        write_transcript_file(transcript_path, metadata, transcript)
    else:
        print(f"  Warning: No transcript available for this video", file=sys.stderr)

    # Write skeleton note
    transcript_stem = transcript_path.stem
    print(f"Creating reference note...", file=sys.stderr)
    write_skeleton_note(note_path, metadata, transcript_stem)

    print(f"Done.", file=sys.stderr)

    return {
        'video_id': video_id,
        'title': metadata['title'],
        'channel': metadata['channel'],
        'duration': format_duration(metadata['duration_seconds']),
        'published': metadata['published'],
        'note_path': str(note_path),
        'transcript_path': str(transcript_path),
        'already_exists': False,
        'transcript_length': transcript_length,
    }


def save_summary_mode(vault_root: Path, url: str, summary_json_path: str) -> dict:
    """Load summary JSON and inject into existing note."""

    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    with open(summary_json_path, 'r') as f:
        summary_data = json.load(f)

    # Reconstruct note path from metadata (need to find existing note)
    # Strategy: search REFERENCES/ for a note containing the video URL
    references_dir = vault_root / 'REFERENCES'
    note_path = None

    if references_dir.exists():
        canonical_url = f"https://youtube.com/watch?v={video_id}"
        for md_file in references_dir.rglob('*.md'):
            content = md_file.read_text()
            if canonical_url in content or f"v={video_id}" in content:
                note_path = md_file
                break

    if note_path is None:
        raise FileNotFoundError(
            f"No existing note found for video {video_id}. "
            "Run without --save-summary first to create the note."
        )

    print(f"Saving summary to: {note_path}", file=sys.stderr)
    save_summary(note_path, summary_data)
    print(f"Done.", file=sys.stderr)

    return {
        'video_id': video_id,
        'note_path': str(note_path),
        'saved': True,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Fetch YouTube transcript and create Obsidian reference note'
    )
    parser.add_argument('vault_root', help='Path to Obsidian vault root')
    parser.add_argument('youtube_url', help='YouTube video URL')
    parser.add_argument(
        '--save-summary',
        metavar='JSON_PATH',
        help='Path to summary JSON file; inject into existing note instead of fetching'
    )

    args = parser.parse_args()

    vault_root = Path(args.vault_root)
    if not vault_root.is_dir():
        print(json.dumps({'error': f"Vault root not found: {vault_root}"}))
        sys.exit(1)

    try:
        if args.save_summary:
            result = save_summary_mode(vault_root, args.youtube_url, args.save_summary)
        else:
            result = fetch_mode(vault_root, args.youtube_url)

        print(json.dumps(result, indent=2))

    except ImportError as e:
        print(json.dumps({'error': str(e), 'hint': 'pip install youtube-transcript-api yt-dlp'}))
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({'error': f"Unexpected error: {e}"}))
        sys.exit(1)


if __name__ == '__main__':
    main()
