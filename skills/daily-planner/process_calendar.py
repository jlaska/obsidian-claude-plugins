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

    # Skip if user hasn't accepted (only create notes for accepted meetings)
    attendees = event.get('attendees', [])
    for attendee in attendees:
        if attendee.get('email') == user_email:
            if attendee.get('responseStatus') != 'accepted':
                return True  # Skip - only create notes for accepted meetings

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


def html_to_markdown(html_content: str) -> str:
    """Convert HTML content to Obsidian-compatible markdown."""
    # Convert <a href="url">text</a> to [text](url)
    html_content = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
        r'[\2](\1)',
        html_content,
        flags=re.IGNORECASE
    )

    # Convert <br> and <br/> to newlines
    html_content = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)

    # Remove remaining HTML tags
    html_content = re.sub(r'<[^>]+>', '', html_content)

    # Decode common HTML entities
    html_content = html_content.replace('&amp;', '&')
    html_content = html_content.replace('&lt;', '<')
    html_content = html_content.replace('&gt;', '>')
    html_content = html_content.replace('&nbsp;', ' ')
    html_content = html_content.replace('&quot;', '"')

    return html_content.strip()


def extract_doc_id_from_url(url: str) -> Optional[str]:
    """Extract Google Doc ID from URL.

    Args:
        url: Google Docs URL (e.g., https://docs.google.com/document/d/DOC_ID/edit)

    Returns:
        Document ID or None if not found
    """
    # Match patterns like:
    # https://docs.google.com/document/d/DOC_ID/edit
    # https://docs.google.com/document/d/DOC_ID
    match = re.search(r'/document/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    return None


def _parse_gdoc_to_markdown(raw_json: str) -> str:
    """Convert raw Google Docs API JSON to markdown.

    Preserves bold text as **bold**, converts heading styles, and
    converts Google Docs soft-returns (\\x0b) to paragraph breaks.
    """
    try:
        doc = json.loads(raw_json)
    except json.JSONDecodeError:
        return ''

    body = doc.get('body', {}).get('content', [])
    paras = []

    for elem in body:
        para = elem.get('paragraph', {})
        if not para:
            continue

        style = para.get('paragraphStyle', {}).get('namedStyleType', '')
        elements = para.get('elements', [])

        parts = []
        for e in elements:
            tr = e.get('textRun', {})
            text = tr.get('content', '')
            bold = tr.get('textStyle', {}).get('bold', False)

            # Strip Google Docs Private Use Area icon characters
            text = re.sub(r'[\ue000-\uf8ff]', '', text)
            # Convert \x0b (Google Docs soft return) to paragraph break
            text = text.replace('\x0b', '\n\n')

            if bold and text.strip():
                parts.append(f'**{text.strip()}**')
            else:
                parts.append(text)

        full_text = ''.join(parts).rstrip('\n')

        if not full_text.strip():
            continue

        if style == 'HEADING_1':
            paras.append(f'# {full_text.strip()}')
        elif style == 'HEADING_2':
            paras.append(f'## {full_text.strip()}')
        elif style == 'HEADING_3':
            paras.append(f'### {full_text.strip()}')
        else:
            paras.append(full_text)

    return '\n\n'.join(paras)


def fetch_gemini_doc_content(doc_id: str) -> Optional[str]:
    """Fetch Google Doc content as markdown using raw API JSON.

    Uses --raw flag to get full API response, preserving bold text and
    paragraph structure. Falls back to plain text if raw fetch fails.

    Args:
        doc_id: Google Docs document ID

    Returns:
        Markdown content of the document or None if error
    """
    try:
        result = subprocess.run(
            ['gog', 'docs', 'cat', '--raw', '--results-only', doc_id],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            md = _parse_gdoc_to_markdown(result.stdout)
            if md:
                return md
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  ⚠️  Failed to fetch Gemini doc {doc_id}: {e}")
    return None


def strip_gemini_boilerplate(content: str) -> str:
    """Strip trailing boilerplate text from Gemini doc content."""
    boilerplate_patterns = [
        r'\nYou should review Gemini\'s notes.*$',
        r'\nPlease provide feedback.*$',
        r'\nThis summary was generated by Gemini.*$',
    ]
    for pattern in boilerplate_patterns:
        content = re.sub(pattern, '', content, flags=re.DOTALL | re.IGNORECASE)
    return content.strip()


def extract_gemini_sections(content: str) -> Dict[str, str]:
    """Extract Summary, Details, and Suggested next steps sections from Gemini transcript.

    Args:
        content: Plain text content from Gemini document

    Returns:
        Dict with 'summary', 'details', and 'next_steps' keys (may be empty strings)
    """
    content = strip_gemini_boilerplate(content)
    sections = {'summary': '', 'details': '', 'next_steps': ''}

    # Find Summary section (heading may have optional ### prefix)
    summary_match = re.search(
        r'(?:^|\n)#{0,6}\s*Summary\s*\n+(.*?)(?=\n+#{0,6}\s*(?:Details|Suggested next steps|Action items)\b|\Z)',
        content, re.DOTALL | re.IGNORECASE
    )
    if summary_match:
        sections['summary'] = summary_match.group(1).strip()

    # Find Details section
    details_match = re.search(
        r'(?:^|\n)#{0,6}\s*Details\s*\n+(.*?)(?=\n+#{0,6}\s*(?:Suggested next steps|Action items)\b|\Z)',
        content, re.DOTALL | re.IGNORECASE
    )
    if details_match:
        sections['details'] = details_match.group(1).strip()

    # Find Suggested next steps section
    next_steps_match = re.search(
        r'(?:^|\n)#{0,6}\s*Suggested next steps\s*\n+(.*?)(?=\n+#{0,6}\s*Action items\b|\Z)',
        content, re.DOTALL | re.IGNORECASE
    )
    if next_steps_match:
        sections['next_steps'] = next_steps_match.group(1).strip()

    return sections


def build_gemini_section(sections: Dict[str, str]) -> str:
    """Format Gemini sections as markdown.

    Args:
        sections: Dict with 'summary', 'details', and 'next_steps' keys

    Returns:
        Formatted markdown section
    """
    lines = ['## Notes by Gemini', '']

    if sections.get('summary'):
        lines.extend(['### Summary', '', sections['summary'], ''])

    if sections.get('details'):
        lines.extend(['### Details', '', sections['details'], ''])

    if sections.get('next_steps'):
        lines.extend(['### Suggested next steps', '', sections['next_steps'], ''])

    return '\n'.join(lines)


def update_meeting_with_gemini_notes(meeting_file: Path) -> bool:
    """Update meeting file with Gemini transcript notes.

    Args:
        meeting_file: Path to meeting file

    Returns:
        True if notes were added, False if skipped
    """
    if not meeting_file.exists():
        return False

    content = meeting_file.read_text()

    # Extract gemini URL from frontmatter
    gemini_url = None
    for line in content.split('\n'):
        if line.startswith('gemini:'):
            gemini_url = line.split('gemini:')[1].strip()
            break

    if not gemini_url:
        return False

    # Extract doc ID from URL
    doc_id = extract_doc_id_from_url(gemini_url)
    if not doc_id:
        print(f"  ⚠️  Could not extract doc ID from: {gemini_url}")
        return False

    # Fetch transcript content
    print(f"  📝 Fetching Gemini transcript...")
    doc_content = fetch_gemini_doc_content(doc_id)
    if not doc_content:
        return False

    # Extract Summary, Details, and Suggested next steps sections
    sections = extract_gemini_sections(doc_content)
    if not sections['summary'] and not sections['details'] and not sections['next_steps']:
        print(f"  ⚠️  No Summary, Details, or Suggested next steps sections found in transcript")
        return False

    # Build Gemini section
    gemini_section = build_gemini_section(sections)

    # If ## Notes by Gemini already exists, replace its content (idempotent update)
    if '## Notes by Gemini' in content:
        section_start = content.find('## Notes by Gemini')

        # Also consume any preceding --- separator we may have added previously
        prefix = content[:section_start]
        for sep in ['\n\n---\n', '\n---\n']:
            if prefix.endswith(sep):
                section_start -= len(sep)
                break

        search_from = content.find('## Notes by Gemini') + len('## Notes by Gemini')

        # Find end of section: next --- separator or next heading of level <= 2
        sep_idx = content.find('\n---', search_from)
        heading_match = re.search(r'\n#{1,2} ', content[search_from:])

        section_end = len(content)
        if sep_idx != -1 and (heading_match is None or sep_idx <= search_from + heading_match.start()):
            section_end = sep_idx
        elif heading_match:
            section_end = search_from + heading_match.start()

        new_content = content[:section_start].rstrip() + '\n\n' + gemini_section + content[section_end:]
        meeting_file.write_text(new_content)
        print(f"  ✓ Updated Gemini notes in: {meeting_file.name}")
        return True

    # Insert new section
    # Find insertion point (before ## Recent Meetings, after # Agenda, or at end)
    _rec_match = re.search(r'^## Recent Meetings', content, re.MULTILINE)
    if _rec_match:
        # Insert before ## Recent Meetings (and any preceding --- separator)
        rec_idx = _rec_match.start()
        insert_before = rec_idx
        for sep in ['\n---\n', '\n\n---\n']:
            if content[:rec_idx].endswith(sep):
                insert_before = rec_idx - len(sep)
                break
        new_content = (content[:insert_before].rstrip() + '\n\n' + gemini_section +
                       '\n\n' + content[insert_before:].lstrip())
    elif '# Agenda' in content:
        # Insert after # Agenda section
        agenda_idx = content.find('# Agenda')
        next_section = content.find('\n#', agenda_idx + 8)
        separator_idx = content.find('\n---', agenda_idx + 8)

        # Use whichever comes first (or end of file)
        insert_idx = len(content)
        if next_section != -1 and (separator_idx == -1 or next_section < separator_idx):
            insert_idx = next_section
        elif separator_idx != -1:
            insert_idx = separator_idx

        new_content = content[:insert_idx].rstrip() + '\n\n' + gemini_section + '\n\n' + content[insert_idx:].lstrip()
    else:
        # Append at end
        new_content = content.rstrip() + '\n\n' + gemini_section + '\n'

    meeting_file.write_text(new_content)
    print(f"  ✓ Added Gemini notes to: {meeting_file.name}")
    return True


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


def classify_attachments(event: Dict) -> Dict[str, list]:
    """Classify event attachments into categories.

    Returns:
        Dict with keys: agenda, minutes, recording, gemini, slides, other.
        Each value is a list of URLs.
    """
    result = {'agenda': [], 'minutes': [], 'recording': [], 'gemini': [], 'slides': [], 'other': []}

    for attachment in event.get('attachments', []):
        file_url = attachment.get('fileUrl', '')
        file_id = attachment.get('fileId', '')

        drive_file = get_drive_file_info(file_id) if file_id else None

        if drive_file:
            name = drive_file.get('name', '').lower()
            mime_type = drive_file.get('mimeType', '')
            web_view_link = drive_file.get('webViewLink', file_url)

            if 'gemini' in name:
                result['gemini'].append(web_view_link)
            elif 'recording' in name:
                result['recording'].append(web_view_link)
            elif 'minutes' in name or 'summary' in name or 'recap' in name:
                result['minutes'].append(web_view_link)
            elif mime_type == 'application/vnd.google-apps.presentation' or \
                 'slides' in name or 'presentation' in name or 'deck' in name:
                result['slides'].append(web_view_link)
            elif 'notes' in name or 'agenda' in name or '1:1' in name or '1-1' in name:
                result['agenda'].append(web_view_link)
            else:
                result['other'].append(web_view_link)
        else:
            # Fallback to calendar attachment title if Drive lookup fails
            title = attachment.get('title', '').lower()

            if 'transcript' in title or 'gemini' in title or 'gemini' in file_url:
                result['gemini'].append(file_url)
            elif 'recording' in title:
                result['recording'].append(file_url)
            elif 'minutes' in title or 'summary' in title or 'recap' in title:
                result['minutes'].append(file_url)
            elif 'notes' in title or 'agenda' in title or '1:1' in title or '1-1' in title:
                result['agenda'].append(file_url)
            elif 'docs.google.com' in file_url:
                result['agenda'].append(file_url)
            else:
                result['other'].append(file_url)

    return result


def update_frontmatter_with_missing_properties(content: str, new_props: Dict[str, str]) -> str:
    """Add missing frontmatter properties to file content.

    Never overwrites existing values — only adds properties that are absent.

    Args:
        content: Full file content including frontmatter
        new_props: Dict of {key: value} pairs to add if missing

    Returns:
        Updated file content
    """
    if not content.startswith('---'):
        return content

    end = content.find('---', 3)
    if end == -1:
        return content

    frontmatter_text = content[3:end]

    # Find which keys already exist
    existing_keys = set()
    for line in frontmatter_text.split('\n'):
        if line and not line.startswith(' ') and ':' in line:
            key = line.split(':', 1)[0].strip()
            if key:
                existing_keys.add(key)

    # Build list of missing properties to add
    additions = [f'{k}: {v}' for k, v in new_props.items() if k not in existing_keys and v]

    if not additions:
        return content

    new_frontmatter = frontmatter_text.rstrip() + '\n' + '\n'.join(additions) + '\n'
    return '---' + new_frontmatter + '---' + content[end + 3:]


def create_meeting_note(event: Dict, vault_root: Path, date_format: str) -> Optional[Tuple[str, Path]]:
    """
    Create or update a meeting note file.

    Returns: Tuple of (start_time, Path) to created/updated file, or None if skipped
    """
    # Get file path
    meeting_file = get_meeting_file_path(event, vault_root, date_format)

    # Match attendees to people
    attendees = event.get('attendees', [])
    # Filter out calendar owner (marked with self: true by Google Calendar API)
    non_self_attendees = [a for a in attendees if not a.get('self')]
    attendee_links = []
    for attendee in non_self_attendees:
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
    classified = classify_attachments(event)
    agenda_links = classified['agenda']
    minutes_links = classified['minutes']
    recording_links = classified['recording']
    gemini_links = classified['gemini']
    slides_links = classified['slides']
    other_links = classified['other']

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
    recurring_event_id = event.get('recurringEventId', '').strip()
    if recurring_event_id:
        # Strip the _R<timestamp> suffix (added when a series is edited) to keep the base series ID
        recurring_event_id = recurring_event_id.split('_R')[0]
        frontmatter_lines.append(f'recurringEventId: {recurring_event_id}')
    if html_link:
        frontmatter_lines.append(f'URL: {html_link}')
    frontmatter_lines.append('---')

    # Build body from template
    template_body = load_meeting_template(vault_root)

    # Inject calendar description into Agenda section if present
    if description:
        # Convert HTML to markdown
        description = html_to_markdown(description)
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
        print(f"  ⚠️  Meeting file already exists: {meeting_file.name}")

        # Build dict of new attachment properties from this run
        new_props = {}
        if gemini_links:
            new_props['gemini'] = gemini_links[0]
        if agenda_links:
            new_props['agenda'] = agenda_links[0]
        if minutes_links:
            new_props['minutes'] = minutes_links[0]
        if recording_links:
            new_props['recording'] = recording_links[0]
        if slides_links:
            new_props['slides'] = slides_links[0]
        if other_links:
            new_props['attachments'] = other_links[0]
        recurring_event_id = event.get('recurringEventId', '').strip()
        if recurring_event_id:
            new_props['recurringEventId'] = recurring_event_id.split('_R')[0]

        # Add any missing frontmatter properties (never overwrite existing values)
        if new_props:
            existing_content = meeting_file.read_text()
            updated_content = update_frontmatter_with_missing_properties(existing_content, new_props)
            if updated_content != existing_content:
                meeting_file.write_text(updated_content)
                print(f"  ✓ Updated frontmatter with missing properties")

        # Try to add/update Gemini notes
        update_meeting_with_gemini_notes(meeting_file)
        return (start_dt, meeting_file)

    meeting_file.write_text(content)
    print(f"  ✓ Created meeting note: {meeting_file.name}")

    # Try to add Gemini notes immediately if available
    if gemini_links:
        update_meeting_with_gemini_notes(meeting_file)

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


def read_meeting_frontmatter(meeting_file: Path) -> dict:
    """Extract start, end, and attendees from meeting file YAML frontmatter."""
    result: dict = {'start': '', 'end': '', 'attendees': [], 'gemini': ''}
    if not meeting_file.exists():
        return result
    lines = meeting_file.read_text().split('\n')
    in_frontmatter = False
    in_attendees = False
    for line in lines:
        if line.strip() == '---':
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                break
        if not in_frontmatter:
            continue
        if line.startswith('start:'):
            result['start'] = line.split('start:', 1)[1].strip()
            in_attendees = False
        elif line.startswith('end:'):
            result['end'] = line.split('end:', 1)[1].strip()
            in_attendees = False
        elif line.startswith('gemini:'):
            result['gemini'] = line.split('gemini:', 1)[1].strip()
            in_attendees = False
        elif line.startswith('attendees:'):
            in_attendees = True
        elif in_attendees and line.startswith('  - '):
            attendee = line[4:].strip().strip('"').strip("'")
            result['attendees'].append(attendee)
        elif in_attendees and not line.startswith(' ') and line.strip():
            in_attendees = False
    return result


def format_time_from_iso(iso_str: str) -> str:
    """Convert ISO datetime string to display format (e.g., '8:30 AM')."""
    if not iso_str:
        return ''
    try:
        dt = datetime.fromisoformat(iso_str)
        hour = dt.strftime('%I').lstrip('0') or '0'
        minute = dt.strftime('%M')
        ampm = dt.strftime('%p')
        return f"{hour}:{minute} {ampm}"
    except ValueError:
        return iso_str


def format_attendees(attendees: list, max_count: int = 6) -> str:
    """Format attendees list as comma-separated string, truncating after max_count."""
    if not attendees:
        return ''
    escaped = [a.replace('|', '\\|') for a in attendees]
    if len(escaped) <= max_count:
        return ', '.join(escaped)
    return ', '.join(escaped[:max_count]) + ', ...'


def build_meetings_table(meeting_rows: list) -> str:
    """Build a markdown table from sorted meeting rows.

    Args:
        meeting_rows: List of (sort_key, stem, frontmatter_dict) tuples sorted by start time.
    """
    lines = [
        '| Time | Meeting | Attendees | Summary |',
        '|------|---------|-----------|---------|',
    ]
    for _, stem, fm in meeting_rows:
        time_str = format_time_from_iso(fm.get('start', ''))
        display_title = re.sub(r'^\d{4}-\d{2}-\d{2} - ', '', stem)
        display_title_escaped = display_title.replace('|', '\\|')
        stem_escaped = stem.replace('|', '\\|')
        meeting_link = f'[[{stem_escaped}\\|{display_title_escaped}]]'
        attendees_str = format_attendees(fm.get('attendees', []))
        gemini_url = fm.get('gemini', '')
        summary_cell = f'[🤖]({gemini_url})' if gemini_url else ''
        lines.append(f'| {time_str} | {meeting_link} | {attendees_str} | {summary_cell} |')
    return '\n'.join(lines)


def update_daily_note(meeting_files: List[Tuple[str, Path]], vault_root: Path, date_format: str, target_date: datetime):
    """
    Update the daily note with a meeting table.

    Adds/updates # 📅 Meetings section with a markdown table showing time, linked
    meeting name, and attendees. Recognizes both table rows and legacy bullet items
    when merging with existing section content.
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

    new_meeting_stems = {meeting_file.stem for _, meeting_file in meeting_files}
    meetings_dir = vault_root / "MEETINGS"

    # Check if meetings section exists
    if '# 📅 Meetings' in content:
        # Find start of section
        start_idx = content.find('# 📅 Meetings')

        # Find end of section (next # header or end of file)
        end_idx = content.find('\n#', start_idx + 1)
        if end_idx == -1:
            end_idx = len(content)

        section_lines = content[start_idx:end_idx].split('\n')
        header = section_lines[0]

        existing_stems: set = set()
        before_table: list = []
        after_table: list = []
        in_content = False
        table_done = False

        for line in section_lines[1:]:
            stripped = line.strip()
            is_table_row = stripped.startswith('|')
            is_bullet = stripped.startswith('- [[')

            if (is_table_row or is_bullet) and not table_done:
                in_content = True
                if is_table_row and '[[' in stripped:
                    # Data row: extract stem from wikilink alias [[stem\|title]] or [[stem]]
                    m = re.search(r'\[\[([^\\\]|]+)', stripped)
                    if m:
                        existing_stems.add(m.group(1).strip())
                elif is_bullet:
                    # Legacy bullet: "- [[stem]]"
                    existing_stems.add(stripped[4:-2])
            elif in_content and not (is_table_row or is_bullet) and not table_done:
                table_done = True
                after_table.append(line)
            elif table_done:
                after_table.append(line)
            elif not in_content:
                before_table.append(line)

        # Merge stems and build sorted rows with frontmatter
        all_stems = existing_stems | new_meeting_stems
        meeting_rows = []
        for stem in all_stems:
            matches = list(meetings_dir.rglob(f"{stem}.md"))
            fm = read_meeting_frontmatter(matches[0]) if matches else {'start': '', 'end': '', 'attendees': []}
            meeting_rows.append((fm.get('start', ''), stem, fm))
        meeting_rows.sort(key=lambda x: x[0])

        # Rebuild section
        new_section_lines = [header]
        if before_table:
            new_section_lines.extend(before_table)
        else:
            new_section_lines.append('')
        new_section_lines.append(build_meetings_table(meeting_rows))
        if after_table:
            new_section_lines.extend(after_table)

        new_section = '\n'.join(new_section_lines)
        content = content[:start_idx] + new_section + content[end_idx:]
    else:
        # Append new section
        meeting_rows = []
        for _, meeting_file in meeting_files:
            fm = read_meeting_frontmatter(meeting_file)
            meeting_rows.append((fm.get('start', ''), meeting_file.stem, fm))
        meeting_rows.sort(key=lambda x: x[0])

        meetings_section = '# 📅 Meetings\n\n' + build_meetings_table(meeting_rows)
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

    # Second pass: Update meeting files with Gemini notes
    # (transcripts may be added after initial calendar fetch)
    if meeting_files:
        print("\nChecking for Gemini transcripts...")
        for _, meeting_file in meeting_files:
            update_meeting_with_gemini_notes(meeting_file)

    print("\n✅ Daily planner complete!")


if __name__ == '__main__':
    main()
