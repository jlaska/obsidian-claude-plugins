#!/usr/bin/env python3
"""
Process Google Calendar events and create Obsidian meeting notes.

This script:
1. Auto-filters calendar events to identify real meetings
2. Matches attendees to existing People files
3. Creates/updates meeting note files
4. Updates the daily note with meeting links
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple


def load_calendar_events(json_path: str) -> List[Dict]:
    """Load calendar events from JSON file."""
    with open(json_path, 'r') as f:
        data = json.load(f)
    return data.get('events', [])


def should_skip_event(event: Dict, user_email: str = "jlaska@redhat.com") -> bool:
    """
    Determine if a calendar event should be skipped (not a real meeting).

    Skip if:
    - Working location event
    - Declined by user
    - No attendees or only yourself
    - Broadcast event (can't see/invite others)
    """
    # Skip working location events
    if event.get('eventType') == 'workingLocation':
        return True

    # Skip if user declined
    attendees = event.get('attendees', [])
    for attendee in attendees:
        if attendee.get('email') == user_email:
            if attendee.get('responseStatus') == 'declined':
                return True

    # Skip if no attendees or only yourself
    if not attendees:
        return True

    non_self_attendees = [a for a in attendees if a.get('email') != user_email]
    if not non_self_attendees:
        return True

    # Skip broadcast events
    if (event.get('guestsCanSeeOtherGuests') is False and
        event.get('guestsCanInviteOthers') is False):
        return True

    return False


def sanitize_title(title: str) -> str:
    """Sanitize meeting title for filesystem."""
    # Replace problematic characters
    title = title.replace('/', ' - ')
    title = title.replace(':', ' - ')
    title = title.replace('|', ' - ')
    # Remove other invalid characters
    title = re.sub(r'[<>:"\\|?*]', '', title)
    # Collapse multiple spaces
    title = re.sub(r'\s+', ' ', title)
    return title.strip()


def match_attendee_to_person(email: str, display_name: str, vault_root: Path) -> str:
    """
    Match calendar attendee to Person file using cascade:
    1. Email match in frontmatter
    2. Name match by filename
    3. Google Directory fallback (gog people search)
    4. Display name

    Returns: "[[Person Name]]" (quoted wikilink)
    """
    people_dir = vault_root / "PEOPLE"

    # 1. Try email match in frontmatter
    if email:
        try:
            result = subprocess.run(
                ['grep', '-r', '-l', f'mail: {email}', str(people_dir)],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                person_file = Path(result.stdout.strip().split('\n')[0])
                person_name = person_file.stem
                return f'"[[{person_name}]]"'
        except (subprocess.TimeoutExpired, Exception):
            pass

    # 2. Try name match by filename
    if display_name:
        person_file = people_dir / f"{display_name}.md"
        if person_file.exists():
            return f'"[[{display_name}]]"'

    # 3. Google Directory fallback
    if email:
        try:
            result = subprocess.run(
                ['gog', 'people', 'search', email, '--json'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                people = data.get('people', [])
                if people:
                    person = people[0]
                    # Try to get full name (direct string, not nested)
                    full_name = person.get('name')
                    if full_name:
                        return f'"[[{full_name}]]"'
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            pass

    # 4. Fallback to display name
    return f'"[[{display_name}]]"'


def get_meeting_file_path(event: Dict, vault_root: Path, date_format: str) -> Path:
    """
    Determine the meeting file path based on date format.

    Format: {meetings_folder}/{format}/YYYY-MM-DD - <Title>.md
    Example: MEETINGS/2026/02-February/2026-02-26 - Team Sync.md
    """
    # Get event date
    start = event.get('start', {})
    date_str = start.get('dateTime') or start.get('date')
    if not date_str:
        raise ValueError(f"Event has no start date: {event.get('summary')}")

    # Parse date
    if 'T' in date_str:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    else:
        dt = datetime.strptime(date_str, '%Y-%m-%d')

    # Build path from date_format
    # Format: YYYY/MM-MMMM/YYYY-MM-DD dddd
    year = dt.strftime('%Y')
    month_num = dt.strftime('%m')
    month_name = dt.strftime('%B')
    date_part = dt.strftime('%Y-%m-%d')

    # Sanitize title
    title = sanitize_title(event.get('summary', 'Untitled'))

    # Build path
    meetings_dir = vault_root / "MEETINGS" / year / f"{month_num}-{month_name}"
    meetings_dir.mkdir(parents=True, exist_ok=True)

    return meetings_dir / f"{date_part} - {title}.md"


def extract_body_from_template(content: str) -> str:
    """Extract body content after frontmatter, removing Templater placeholders."""
    # Remove frontmatter
    if content.startswith('---'):
        end = content.find('---', 3)
        if end != -1:
            content = content[end + 3:].strip()
    # Remove Templater placeholders like <% tp.file.cursor() %>
    content = re.sub(r'<%.*?%>', '', content)
    return content


def load_template(vault_root: Path, template_name: str) -> str:
    """Load template body from vault or fallback to plugin default.

    Args:
        vault_root: Path to Obsidian vault root
        template_name: Name without extension, e.g., "Meeting Template", "Daily Note Template"

    Returns:
        Template body content (without frontmatter), or empty string if not found
    """
    # 1. Try vault's template config
    templates_config = vault_root / ".obsidian" / "templates.json"
    if templates_config.exists():
        try:
            config = json.loads(templates_config.read_text())
            templates_folder = config.get('folder', 'TEMPLATES')
            vault_template = vault_root / templates_folder / f"{template_name}.md"
            if vault_template.exists():
                return extract_body_from_template(vault_template.read_text())
        except (json.JSONDecodeError, Exception):
            pass

    # 2. Fallback to plugin default
    plugin_default = Path(__file__).parent.parent / "obsidian-vault-setup" / "defaults" / "templates" / f"{template_name}.md"
    if plugin_default.exists():
        return extract_body_from_template(plugin_default.read_text())

    # 3. Return empty string (callers provide their own fallback)
    return ""


def get_drive_file_info(file_id: str) -> Optional[Dict]:
    """
    Get file metadata from Google Drive.

    Args:
        file_id: Google Drive file ID

    Returns:
        Dict with file metadata (id, name, mimeType, webViewLink) or None if error
    """
    try:
        result = subprocess.run(
            ['gog', 'drive', 'get', file_id, '--json', '--results-only'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            return data.get('file', {})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    return None


def load_meeting_template(vault_root: Path) -> str:
    """Load Meeting Template body from vault or fallback to plugin default."""
    body = load_template(vault_root, "Meeting Template")
    return body if body else "## Actions\n\n\n## Agenda\n\n"


def load_daily_note_template(vault_root: Path) -> str:
    """Load Daily Note Template body from vault or fallback to plugin default."""
    body = load_template(vault_root, "Daily Note Template")
    return body if body else "# 📅 Meetings\n\n"


def create_meeting_note(event: Dict, vault_root: Path, date_format: str) -> Optional[Tuple[str, Path]]:
    """
    Create or update a meeting note file.

    Returns: Tuple of (start_time, Path) to created/updated file, or None if skipped
    """
    # Get file path
    meeting_file = get_meeting_file_path(event, vault_root, date_format)

    # Match attendees to people
    attendees = event.get('attendees', [])
    attendee_links = []
    for attendee in attendees:
        email = attendee.get('email', '')
        display_name = attendee.get('displayName') or email.split('@')[0]
        person_link = match_attendee_to_person(email, display_name, vault_root)
        attendee_links.append(person_link)

    # Get event details
    start = event.get('start', {})
    end = event.get('end', {})
    start_dt = start.get('dateTime', '')
    end_dt = end.get('dateTime', '')
    gmeet = event.get('hangoutLink', '')
    description = event.get('description', '')
    html_link = event.get('htmlLink', '')

    # Parse attachments
    attachments = event.get('attachments', [])
    agenda_links = []
    minutes_links = []
    recording_links = []
    gemini_links = []
    slides_links = []
    other_links = []

    for attachment in attachments:
        file_url = attachment.get('fileUrl', '')
        file_id = attachment.get('fileId', '')

        # Get detailed metadata from Drive
        drive_file = get_drive_file_info(file_id) if file_id else None

        if drive_file:
            # Use Drive metadata for classification
            name = drive_file.get('name', '').lower()
            mime_type = drive_file.get('mimeType', '')
            web_view_link = drive_file.get('webViewLink', file_url)

            # Classify based on Drive metadata
            if 'gemini' in name:
                gemini_links.append(web_view_link)
            elif 'recording' in name:
                recording_links.append(web_view_link)
            elif 'minutes' in name or 'summary' in name or 'recap' in name:
                minutes_links.append(web_view_link)
            elif mime_type == 'application/vnd.google-apps.presentation' or \
                 'slides' in name or 'presentation' in name or 'deck' in name:
                slides_links.append(web_view_link)
            elif 'notes' in name or 'agenda' in name or '1:1' in name or '1-1' in name:
                agenda_links.append(web_view_link)
            else:
                other_links.append(web_view_link)
        else:
            # Fallback to calendar attachment title if Drive lookup fails
            title = attachment.get('title', '').lower()

            if 'transcript' in title or 'gemini' in file_url:
                gemini_links.append(file_url)
            elif 'recording' in title:
                recording_links.append(file_url)
            elif 'minutes' in title or 'summary' in title or 'recap' in title:
                minutes_links.append(file_url)
            elif 'notes' in title or 'agenda' in title or '1:1' in title or '1-1' in title:
                agenda_links.append(file_url)
            elif 'docs.google.com' in file_url:
                # Default Google Docs to agenda
                agenda_links.append(file_url)
            else:
                other_links.append(file_url)

    # Get created timestamp
    created_dt = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Build frontmatter
    frontmatter_lines = ['---']
    if attendee_links:
        frontmatter_lines.append('attendees:')
        for link in attendee_links:
            frontmatter_lines.append(f'  - {link}')
    frontmatter_lines.append('tags:')
    frontmatter_lines.append('  - Meetings')
    frontmatter_lines.append(f'created: {created_dt}')
    if start_dt:
        frontmatter_lines.append(f'start: {start_dt}')
    if end_dt:
        frontmatter_lines.append(f'end: {end_dt}')
    if gmeet:
        frontmatter_lines.append(f'gmeet: {gmeet}')
    if agenda_links:
        frontmatter_lines.append(f'agenda: {agenda_links[0]}')
    if minutes_links:
        frontmatter_lines.append(f'minutes: {minutes_links[0]}')
    if recording_links:
        frontmatter_lines.append(f'recording: {recording_links[0]}')
    if gemini_links:
        frontmatter_lines.append(f'gemini: {gemini_links[0]}')
    if slides_links:
        frontmatter_lines.append(f'slides: {slides_links[0]}')
    if other_links:
        frontmatter_lines.append(f'attachments: {other_links[0]}')
    if html_link:
        frontmatter_lines.append(f'URL: {html_link}')
    frontmatter_lines.append('---')

    # Build body from template
    template_body = load_meeting_template(vault_root)

    # Inject calendar description into Agenda section if present
    if description:
        # Find the Agenda section and inject description after it
        if '# Agenda' in template_body:
            parts = template_body.split('# Agenda', 1)
            # Find the next section or end of content
            after_agenda = parts[1]
            next_section_idx = after_agenda.find('\n#')
            if next_section_idx != -1:
                # Insert description before next section
                template_body = (parts[0] + '# Agenda\n\n' +
                               description + '\n' +
                               after_agenda[next_section_idx:])
            else:
                # Append description at end of Agenda section
                template_body = parts[0] + '# Agenda\n\n' + description + '\n'
        else:
            # No Agenda section, append description at end
            template_body += f"\n\n# Agenda\n\n{description}\n"

    body_lines = ['', template_body]

    # Write file
    content = '\n'.join(frontmatter_lines + body_lines)

    # Check if file exists - if so, don't overwrite user content
    if meeting_file.exists():
        # TODO: Implement update logic (merge new attachments, update gmeet, etc.)
        print(f"  ⚠️  Meeting file already exists: {meeting_file.name}")
        return (start_dt, meeting_file)

    meeting_file.write_text(content)
    print(f"  ✓ Created meeting note: {meeting_file.name}")
    return (start_dt, meeting_file)


def get_meeting_start_time(meeting_file: Path) -> str:
    """Extract start time from meeting file frontmatter."""
    if not meeting_file.exists():
        return ""
    content = meeting_file.read_text()
    for line in content.split('\n'):
        if line.startswith('start:'):
            return line.split('start:')[1].strip()
    return ""


def update_daily_note(meeting_files: List[Tuple[str, Path]], vault_root: Path, date_format: str, target_date: datetime):
    """
    Update the daily note with meeting links.

    Adds/updates # 📅 Meetings section with wikilinks to meeting files.
    """
    # Build daily note path
    year = target_date.strftime('%Y')
    month_num = target_date.strftime('%m')
    month_name = target_date.strftime('%B')
    day_name = target_date.strftime('%A')
    date_part = target_date.strftime('%Y-%m-%d')

    daily_notes_dir = vault_root / "DAILY_NOTES" / year / f"{month_num}-{month_name}"
    daily_notes_dir.mkdir(parents=True, exist_ok=True)

    daily_note_file = daily_notes_dir / f"{date_part} {day_name}.md"

    # Read existing content or create new
    if daily_note_file.exists():
        content = daily_note_file.read_text()
    else:
        # Create from template
        template_body = load_daily_note_template(vault_root)
        created_dt = datetime.now().strftime('%Y-%m-%d %H:%M')
        content = f"---\ncreated: {created_dt}\ntags:\n  - Daily_Notes\n---\n\n{template_body}"

    # Build meeting links - sort by start time (earliest first)
    sorted_meetings = sorted(meeting_files, key=lambda x: x[0])
    new_meeting_links = []
    for start_time, meeting_file in sorted_meetings:
        # Create wikilink without extension
        link = f"- [[{meeting_file.stem}]]"
        if link not in new_meeting_links:
            new_meeting_links.append(link)

    # Check if meetings section exists
    if '# 📅 Meetings' in content:
        # Find start of section
        start_idx = content.find('# 📅 Meetings')

        # Find end of section (next # header or end of file)
        end_idx = content.find('\n#', start_idx + 1)
        if end_idx == -1:
            end_idx = len(content)

        # Extract the section content
        section_content = content[start_idx:end_idx]
        lines = section_content.split('\n')

        # Separate header, list items, and other content
        header = lines[0]  # '# 📅 Meetings'
        existing_links = set()
        before_list = []  # Content between header and first list item
        after_list = []   # Content after last list item
        in_list = False
        list_ended = False

        for i, line in enumerate(lines[1:], 1):
            # Check if this is a meeting list item
            if line.strip().startswith('- [[') and not list_ended:
                in_list = True
                existing_links.add(line.strip())
            elif in_list and line.strip() and not line.strip().startswith('- [['):
                # Non-list content after list items
                list_ended = True
                after_list.append(line)
            elif list_ended:
                after_list.append(line)
            elif not in_list and line.strip():
                # Content before list
                before_list.append(line)
            elif not in_list:
                # Empty lines before list
                before_list.append(line)

        # Merge existing and new links, then sort by start time
        all_meeting_stems = set()
        for link in existing_links:
            # Extract stem from "- [[Meeting Stem]]"
            stem = link.strip()[4:-2]  # Remove "- [[" and "]]"
            all_meeting_stems.add(stem)
        for link in new_meeting_links:
            stem = link.strip()[4:-2]
            all_meeting_stems.add(stem)

        # Sort all meetings by start time
        meetings_dir = vault_root / "MEETINGS"
        meeting_times = []
        for stem in all_meeting_stems:
            # Find the meeting file
            matches = list(meetings_dir.rglob(f"{stem}.md"))
            if matches:
                start_time = get_meeting_start_time(matches[0])
                meeting_times.append((start_time, f"- [[{stem}]]"))
            else:
                meeting_times.append(("", f"- [[{stem}]]"))

        all_links = [link for _, link in sorted(meeting_times)]

        # Rebuild section
        new_section_lines = [header]

        # Add content before list (if any)
        if before_list:
            new_section_lines.extend(before_list)
        else:
            # If no content before, add blank line after header
            new_section_lines.append('')

        # Add merged meeting links
        new_section_lines.extend(all_links)

        # Add content after list (if any)
        if after_list:
            new_section_lines.extend(after_list)

        # Replace section
        new_section = '\n'.join(new_section_lines)
        content = content[:start_idx] + new_section + content[end_idx:]
    else:
        # Append new section - new_meeting_links is already sorted by start time
        meetings_section = '# 📅 Meetings\n\n' + '\n'.join(new_meeting_links)
        content = content.rstrip() + '\n\n' + meetings_section + '\n'

    # Write updated daily note
    daily_note_file.write_text(content)
    print(f"\n✓ Updated daily note: {daily_note_file.name}")


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python3 process_calendar.py <vault_root> <calendar_json_path> [date]")
        sys.exit(1)

    vault_root = Path(sys.argv[1])
    calendar_json_path = sys.argv[2]
    target_date = datetime.strptime(sys.argv[3], '%Y-%m-%d') if len(sys.argv) > 3 else datetime.now()

    # Load calendar events
    print(f"Loading calendar events from {calendar_json_path}...")
    events = load_calendar_events(calendar_json_path)
    print(f"Found {len(events)} total events")

    # Filter and process events
    meeting_files = []
    skipped_count = 0

    for event in events:
        summary = event.get('summary', 'Untitled')

        # Skip non-meetings
        if should_skip_event(event):
            skipped_count += 1
            continue

        # Create meeting note
        print(f"\nProcessing: {summary}")
        try:
            result = create_meeting_note(event, vault_root, "YYYY/MM-MMMM/YYYY-MM-DD dddd")
            if result:
                meeting_files.append(result)
        except Exception as e:
            print(f"  ✗ Error creating meeting note: {e}")

    print(f"\n\nProcessed {len(meeting_files)} meetings (skipped {skipped_count} non-meetings)")

    # Update daily note
    if meeting_files:
        update_daily_note(meeting_files, vault_root, "YYYY/MM-MMMM/YYYY-MM-DD dddd", target_date)

    print("\n✅ Daily planner complete!")


if __name__ == '__main__':
    main()
