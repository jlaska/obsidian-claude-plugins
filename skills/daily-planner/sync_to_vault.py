#!/usr/bin/env python3
"""
Sync calendar events to the Obsidian vault.

Takes filtered calendar events JSON (from discover_events.py) and:
1. Matches attendees to PEOPLE/ files
2. Creates or updates meeting note files with rich frontmatter
3. Fetches Gemini notes from Google Docs and injects them
4. Captures Gemini transcripts to TRANSCRIPTS/
5. Builds/updates the daily note's # Meetings table
6. Detects and reports stale/cancelled meetings

This script replaces the file-creation portion of process_calendar.py.

Usage:
    python3 sync_to_vault.py --vault-root /path/to/vault --events-json events.json
    python3 sync_to_vault.py --vault-root /path/to/vault --events-json events.json --date 2026-06-05
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from vault_utils import (
    extract_doc_id_from_url,
    html_to_markdown,
    load_template,
    sanitize_title,
    update_frontmatter_values,
    update_frontmatter_with_missing_properties,
)


# ---------------------------------------------------------------------------
# Google API helpers (gog CLI)
# ---------------------------------------------------------------------------

def _run(cmd: list, timeout: int = 30) -> Optional[subprocess.CompletedProcess]:
    """Run a subprocess and return the result, or None on failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f'  ⚠️  Command failed {" ".join(cmd[:3])}: {e}', file=sys.stderr)
        return None


def get_drive_file_info(file_id: str) -> Optional[Dict]:
    """Fetch Drive file metadata via gog."""
    result = _run(['gog', 'drive', 'get', file_id, '--json', '--results-only'])
    if result and result.returncode == 0 and result.stdout.strip():
        try:
            data = json.loads(result.stdout)
            return data.get('file', {})
        except json.JSONDecodeError:
            pass
    return None


def classify_attachments(event: Dict) -> Dict[str, list]:
    """Classify event attachments into categories."""
    result = {'agenda': [], 'minutes': [], 'recording': [], 'gemini': [], 'slides': [], 'other': []}
    for attachment in event.get('attachments', []):
        file_url = attachment.get('fileUrl', '')
        file_id = attachment.get('fileId', '')
        drive_file = get_drive_file_info(file_id) if file_id else None

        if drive_file:
            name = drive_file.get('name', '').lower()
            mime_type = drive_file.get('mimeType', '')
            url = drive_file.get('webViewLink', file_url)
            if 'gemini' in name:
                result['gemini'].append(url)
            elif 'recording' in name:
                result['recording'].append(url)
            elif any(k in name for k in ('minutes', 'summary', 'recap')):
                result['minutes'].append(url)
            elif (mime_type == 'application/vnd.google-apps.presentation'
                  or any(k in name for k in ('slides', 'presentation', 'deck'))):
                result['slides'].append(url)
            elif any(k in name for k in ('notes', 'agenda', '1:1', '1-1')):
                result['agenda'].append(url)
            else:
                result['other'].append(url)
        else:
            title = attachment.get('title', '').lower()
            if 'transcript' in title or 'gemini' in title or 'gemini' in file_url:
                result['gemini'].append(file_url)
            elif 'recording' in title:
                result['recording'].append(file_url)
            elif any(k in title for k in ('minutes', 'summary', 'recap')):
                result['minutes'].append(file_url)
            elif any(k in title for k in ('notes', 'agenda', '1:1', '1-1')):
                result['agenda'].append(file_url)
            elif 'docs.google.com' in file_url:
                result['agenda'].append(file_url)
            else:
                result['other'].append(file_url)
    return result


def match_attendee_to_person(email: str, display_name: str, vault_root: Path) -> str:
    """Match calendar attendee email to a PEOPLE/ wikilink."""
    people_dir = vault_root / 'PEOPLE'

    # 1. Email in frontmatter
    if email:
        result = _run(['grep', '-r', '-l', f'mail: {email}', str(people_dir)], timeout=5)
        if result and result.returncode == 0 and result.stdout.strip():
            person_file = Path(result.stdout.strip().split('\n')[0])
            return f'"[[{person_file.stem}]]"'

    # 2. Filename match
    if display_name:
        person_file = people_dir / f'{display_name}.md'
        if person_file.exists():
            return f'"[[{display_name}]]"'

    # 3. gog people search
    if email:
        result = _run(['gog', 'people', 'search', email, '--json'], timeout=10)
        if result and result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                people = data.get('people', [])
                if people:
                    full_name = people[0].get('name')
                    if full_name:
                        return f'"[[{full_name}]]"'
            except json.JSONDecodeError:
                pass

    # 4. Display name fallback
    return f'"[[{display_name}]]"'


# ---------------------------------------------------------------------------
# Gemini notes and transcript fetching
# ---------------------------------------------------------------------------

def _parse_gdoc_to_markdown(raw_json: str) -> str:
    """Convert raw Google Docs API JSON to markdown."""
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
            text = re.sub(r'[-]', '', text)
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
    """Fetch Google Doc and return as markdown."""
    result = _run(['gog', 'docs', 'cat', '--raw', '--results-only', doc_id], timeout=15)
    if result and result.returncode == 0 and result.stdout.strip():
        md = _parse_gdoc_to_markdown(result.stdout)
        if md:
            return md
    return None


def _strip_gemini_boilerplate(content: str) -> str:
    boilerplate_patterns = [
        r"\nYou should review Gemini's notes.*$",
        r'\nPlease provide feedback.*$',
        r'\nThis summary was generated by Gemini.*$',
    ]
    for pattern in boilerplate_patterns:
        content = re.sub(pattern, '', content, flags=re.DOTALL | re.IGNORECASE)
    return content.strip()


def extract_gemini_sections(content: str) -> Dict[str, str]:
    """Extract Summary, Details, and Suggested next steps from Gemini doc."""
    content = _strip_gemini_boilerplate(content)
    sections = {'summary': '', 'details': '', 'next_steps': ''}
    summary_match = re.search(
        r'(?:^|\n)#{0,6}\s*Summary\s*\n+(.*?)(?=\n+#{0,6}\s*(?:Details|Suggested next steps|Action items)\b|\Z)',
        content, re.DOTALL | re.IGNORECASE
    )
    if summary_match:
        sections['summary'] = summary_match.group(1).strip()
    details_match = re.search(
        r'(?:^|\n)#{0,6}\s*Details\s*\n+(.*?)(?=\n+#{0,6}\s*(?:Suggested next steps|Action items)\b|\Z)',
        content, re.DOTALL | re.IGNORECASE
    )
    if details_match:
        sections['details'] = details_match.group(1).strip()
    next_steps_match = re.search(
        r'(?:^|\n)#{0,6}\s*Suggested next steps\s*\n+(.*?)(?=\n+#{0,6}\s*Action items\b|\Z)',
        content, re.DOTALL | re.IGNORECASE
    )
    if next_steps_match:
        sections['next_steps'] = next_steps_match.group(1).strip()
    return sections


def build_gemini_section(sections: Dict[str, str]) -> str:
    lines = ['## Notes by Gemini', '']
    if sections.get('summary'):
        lines.extend(['### Summary', '', sections['summary'], ''])
    if sections.get('details'):
        lines.extend(['### Details', '', sections['details'], ''])
    if sections.get('next_steps'):
        lines.extend(['### Suggested next steps', '', sections['next_steps'], ''])
    return '\n'.join(lines)


def update_meeting_with_gemini_notes(meeting_file: Path) -> bool:
    """Fetch Gemini notes and inject/update ## Notes by Gemini section."""
    if not meeting_file.exists():
        return False
    content = meeting_file.read_text()
    gemini_url = None
    for line in content.split('\n'):
        if line.startswith('gemini:'):
            gemini_url = line.split('gemini:')[1].strip()
            break
    if not gemini_url:
        return False
    doc_id = extract_doc_id_from_url(gemini_url)
    if not doc_id:
        return False
    print('  📝 Fetching Gemini transcript...')
    doc_content = fetch_gemini_doc_content(doc_id)
    if not doc_content:
        return False
    sections = extract_gemini_sections(doc_content)
    if not any(sections.values()):
        return False
    gemini_section = build_gemini_section(sections)

    if '## Notes by Gemini' in content:
        section_start = content.find('## Notes by Gemini')
        prefix = content[:section_start]
        for sep in ['\n\n---\n', '\n---\n']:
            if prefix.endswith(sep):
                section_start -= len(sep)
                break
        search_from = content.find('## Notes by Gemini') + len('## Notes by Gemini')
        sep_idx = content.find('\n---', search_from)
        heading_match = re.search(r'\n#{1,2} ', content[search_from:])
        section_end = len(content)
        if sep_idx != -1 and (heading_match is None or sep_idx <= search_from + heading_match.start()):
            section_end = sep_idx
        elif heading_match:
            section_end = search_from + heading_match.start()
        new_content = content[:section_start].rstrip() + '\n\n' + gemini_section + content[section_end:]
        meeting_file.write_text(new_content)
        print(f'  ✓ Updated Gemini notes: {meeting_file.name}')
        return True

    # Insert before ## Recent Meetings, after ## Agenda, or at end
    rec_match = re.search(r'^## Recent Meetings', content, re.MULTILINE)
    if rec_match:
        rec_idx = rec_match.start()
        insert_before = rec_idx
        for sep in ['\n---\n', '\n\n---\n']:
            if content[:rec_idx].endswith(sep):
                insert_before = rec_idx - len(sep)
                break
        new_content = (content[:insert_before].rstrip() + '\n\n' + gemini_section
                       + '\n\n' + content[insert_before:].lstrip())
    elif '## Agenda' in content:
        agenda_idx = content.find('## Agenda')
        next_section = content.find('\n#', agenda_idx + 8)
        separator_idx = content.find('\n---', agenda_idx + 8)
        insert_idx = len(content)
        if next_section != -1 and (separator_idx == -1 or next_section < separator_idx):
            insert_idx = next_section
        elif separator_idx != -1:
            insert_idx = separator_idx
        new_content = content[:insert_idx].rstrip() + '\n\n' + gemini_section + '\n\n' + content[insert_idx:].lstrip()
    else:
        new_content = content.rstrip() + '\n\n' + gemini_section + '\n'
    meeting_file.write_text(new_content)
    print(f'  ✓ Added Gemini notes: {meeting_file.name}')
    return True


def _parse_transcript_elements(elements: list, timestamp: str) -> List[str]:
    """Parse a Google Docs transcript paragraph into diarized lines."""
    lines = []
    current_speaker: Optional[str] = None
    utterance_parts: List[str] = []
    for elem in elements:
        tr = elem.get('textRun', {})
        if not tr:
            continue
        raw = tr.get('content', '')
        raw = re.sub(r'[-]', '', raw)
        stripped = raw.rstrip('\x0b\n').rstrip()
        if stripped.endswith(':') and not raw.startswith(' ') and not raw.startswith('\xa0'):
            if current_speaker is not None and utterance_parts:
                utterance = ' '.join(''.join(utterance_parts).split()).strip()
                if utterance:
                    lines.append(f'[{timestamp}] {current_speaker} {utterance}')
            current_speaker = stripped
            utterance_parts = []
        else:
            text = raw.replace('\x0b', ' ').replace('\xa0', ' ')
            utterance_parts.append(text)
    if current_speaker is not None and utterance_parts:
        utterance = ' '.join(''.join(utterance_parts).split()).strip()
        if utterance:
            lines.append(f'[{timestamp}] {current_speaker} {utterance}')
    return lines


def fetch_and_parse_transcript_tab(doc_id: str) -> Optional[str]:
    """Fetch Transcript tab from Gemini doc and return diarized text."""
    result = _run(
        ['gog', 'docs', 'cat', '--tab', 'Transcript', '--raw', '--results-only', doc_id],
        timeout=30,
    )
    if not result or result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    body = []
    for tab in doc.get('tabs', []):
        props = tab.get('tabProperties', {})
        if props.get('title', '').lower() == 'transcript':
            body = tab.get('documentTab', {}).get('body', {}).get('content', [])
            break
    if not body:
        return None

    lines = []
    current_timestamp = '00:00:00'
    in_header = True
    for elem in body:
        para = elem.get('paragraph', {})
        if not para:
            continue
        style = para.get('paragraphStyle', {}).get('namedStyleType', '')
        elements = para.get('elements', [])
        text_parts = [e.get('textRun', {}).get('content', '') for e in elements if e.get('textRun')]
        full_text = ''.join(text_parts).strip()
        if not full_text:
            continue
        if style == 'HEADING_3':
            if re.match(r'^\d{2}:\d{2}:\d{2}$', full_text):
                current_timestamp = full_text
                in_header = False
            continue
        if in_header:
            continue
        if re.match(r'Transcription ended after', full_text, re.IGNORECASE):
            break
        if re.match(r'This editable transcript', full_text, re.IGNORECASE):
            break
        speaker_lines = _parse_transcript_elements(elements, current_timestamp)
        lines.extend(speaker_lines)

    return '\n'.join(lines) if lines else None


def write_meeting_transcript(vault_root: Path, meeting_file: Path, transcript_content: str, gemini_url: str) -> Optional[Path]:
    """Write diarized transcript to TRANSCRIPTS/."""
    stem = meeting_file.stem
    transcript_path = vault_root / 'TRANSCRIPTS' / f'{stem} - transcript.md'
    if transcript_path.exists():
        return None
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    date_match = re.match(r'^(\d{4}-\d{2}-\d{2})', stem)
    created = date_match.group(1) if date_match else ''
    fm_lines = ['---', 'tags:', '  - Attachments', '  - Transcript']
    if created:
        fm_lines.append(f'created: {created}')
    if gemini_url:
        fm_lines.append(f'gemini: {gemini_url}')
    fm_lines.extend(['---', '', transcript_content, ''])
    transcript_path.write_text('\n'.join(fm_lines))
    return transcript_path


def capture_meeting_transcript(meeting_file: Path, vault_root: Path) -> bool:
    """Fetch and save the Transcript tab from the meeting's Gemini doc."""
    if not meeting_file.exists():
        return False
    content = meeting_file.read_text()
    if re.search(r'^transcript:', content, re.MULTILINE):
        return False
    gemini_url = None
    for line in content.split('\n'):
        if line.startswith('gemini:'):
            gemini_url = line.split('gemini:')[1].strip()
            break
    if not gemini_url:
        return False
    doc_id = extract_doc_id_from_url(gemini_url)
    if not doc_id:
        return False
    print('  📝 Fetching Gemini transcript tab...')
    transcript_content = fetch_and_parse_transcript_tab(doc_id)
    if not transcript_content:
        print('  ⚠️  No Transcript tab found')
        return False
    transcript_file = write_meeting_transcript(vault_root, meeting_file, transcript_content, gemini_url)
    if transcript_file is None:
        return False
    transcript_stem = transcript_file.stem
    updated = update_frontmatter_with_missing_properties(content, {'transcript': f'"[[{transcript_stem}]]"'})
    meeting_file.write_text(updated)
    print(f'  ✓ Transcript saved: {transcript_file.name}')
    return True


# ---------------------------------------------------------------------------
# Meeting file path computation
# ---------------------------------------------------------------------------

def get_meeting_file_path(event: Dict, vault_root: Path) -> Path:
    """Compute the meeting file path from event data."""
    start = event.get('start', {})
    date_str = start.get('dateTime') or start.get('date')
    if not date_str:
        raise ValueError(f"Event has no start date: {event.get('summary')}")
    if 'T' in date_str:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    else:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
    year = dt.strftime('%Y')
    month_num = dt.strftime('%m')
    month_name = dt.strftime('%B')
    date_part = dt.strftime('%Y-%m-%d')
    title = sanitize_title(event.get('summary', 'Untitled'))
    meetings_dir = vault_root / 'MEETINGS' / year / f'{month_num}-{month_name}'
    meetings_dir.mkdir(parents=True, exist_ok=True)
    return meetings_dir / f'{date_part} - {title}.md'


# ---------------------------------------------------------------------------
# Meeting note creation/update
# ---------------------------------------------------------------------------

def create_or_update_meeting_note(
    event: Dict,
    vault_root: Path,
    user_emails: Set[str],
    skill_base_dir: Optional[Path] = None,
) -> Optional[Tuple[str, Path]]:
    """Create or update a meeting note file. Returns (start_dt, path) or None."""
    meeting_file = get_meeting_file_path(event, vault_root)

    # Attendee matching
    attendees = event.get('attendees', [])
    non_self = [a for a in attendees if not a.get('self') and a.get('email', '').lower() not in user_emails]
    attendee_links = []
    for att in non_self:
        email = att.get('email', '')
        display_name = att.get('displayName') or email.split('@')[0]
        attendee_links.append(match_attendee_to_person(email, display_name, vault_root))

    start = event.get('start', {})
    end = event.get('end', {})
    start_dt = start.get('dateTime', '')
    end_dt = end.get('dateTime', '')
    gmeet = event.get('hangoutLink', '')
    description = event.get('description', '')
    html_link = event.get('htmlLink', '')

    classified = classify_attachments(event)

    recurring_id = event.get('recurringEventId', '').strip()
    if recurring_id:
        recurring_id = recurring_id.split('_R')[0]

    if meeting_file.exists():
        existing = meeting_file.read_text()
        time_updates = {}
        if start_dt:
            time_updates['start'] = start_dt
        if end_dt:
            time_updates['end'] = end_dt
        if time_updates:
            updated = update_frontmatter_values(existing, time_updates)
            if updated != existing:
                meeting_file.write_text(updated)
                existing = updated
                print(f'  ✓ Updated meeting times')

        new_props: Dict[str, str] = {}
        if classified['gemini']:
            new_props['gemini'] = classified['gemini'][0]
        if classified['agenda']:
            new_props['agenda'] = classified['agenda'][0]
        if classified['minutes']:
            new_props['minutes'] = classified['minutes'][0]
        if classified['recording']:
            new_props['recording'] = classified['recording'][0]
        if classified['slides']:
            new_props['slides'] = classified['slides'][0]
        if classified['other']:
            new_props['attachments'] = classified['other'][0]
        if recurring_id:
            new_props['recurringEventId'] = recurring_id
        if new_props:
            updated = update_frontmatter_with_missing_properties(existing, new_props)
            if updated != existing:
                meeting_file.write_text(updated)
                print(f'  ✓ Updated frontmatter')
        update_meeting_with_gemini_notes(meeting_file)
        print(f'  ⚠️  Meeting file already exists: {meeting_file.name}')
        return (start_dt, meeting_file)

    # Build frontmatter for new file
    fm_lines = ['---']
    if attendee_links:
        fm_lines.append('attendees:')
        for link in attendee_links:
            fm_lines.append(f'  - {link}')
    fm_lines.extend(['tags:', '  - Meetings', f'created: {datetime.now().strftime("%Y-%m-%d %H:%M")}'])
    if start_dt:
        fm_lines.append(f'start: {start_dt}')
    if end_dt:
        fm_lines.append(f'end: {end_dt}')
    if gmeet:
        fm_lines.append(f'gmeet: {gmeet}')
    if classified['agenda']:
        fm_lines.append(f'agenda: {classified["agenda"][0]}')
    if classified['minutes']:
        fm_lines.append(f'minutes: {classified["minutes"][0]}')
    if classified['recording']:
        fm_lines.append(f'recording: {classified["recording"][0]}')
    if classified['gemini']:
        fm_lines.append(f'gemini: {classified["gemini"][0]}')
    if classified['slides']:
        fm_lines.append(f'slides: {classified["slides"][0]}')
    if classified['other']:
        fm_lines.append(f'attachments: {classified["other"][0]}')
    if recurring_id:
        fm_lines.append(f'recurringEventId: {recurring_id}')
    if html_link:
        fm_lines.append(f'URL: {html_link}')
    fm_lines.append('---')

    # Build body from template
    template_body = load_template(vault_root, 'Meeting Template', skill_base_dir) or '## Actions\n\n\n## Agenda\n\n'
    if description:
        desc_md = html_to_markdown(description)
        if '## Agenda' in template_body:
            parts = template_body.split('## Agenda', 1)
            after = parts[1]
            next_idx = after.find('\n#')
            if next_idx != -1:
                template_body = parts[0] + '## Agenda\n\n' + desc_md + '\n' + after[next_idx:]
            else:
                template_body = parts[0] + '## Agenda\n\n' + desc_md + '\n'
        else:
            template_body += f'\n\n## Agenda\n\n{desc_md}\n'

    content = '\n'.join(fm_lines) + '\n\n' + template_body
    meeting_file.write_text(content)
    print(f'  ✓ Created meeting note: {meeting_file.name}')
    if classified['gemini']:
        update_meeting_with_gemini_notes(meeting_file)
    return (start_dt, meeting_file)


# ---------------------------------------------------------------------------
# Daily note table
# ---------------------------------------------------------------------------

def format_time_from_iso(iso_str: str) -> str:
    """Convert ISO datetime to display format like '8:30 AM'."""
    if not iso_str:
        return ''
    try:
        dt = datetime.fromisoformat(iso_str)
        hour = dt.strftime('%I').lstrip('0') or '0'
        minute = dt.strftime('%M')
        ampm = dt.strftime('%p')
        return f'{hour}:{minute} {ampm}'
    except ValueError:
        return iso_str


def _read_meeting_fm(meeting_file: Path) -> Dict:
    """Read start, end, attendees, gemini from meeting frontmatter."""
    result: Dict = {'start': '', 'end': '', 'attendees': [], 'gemini': ''}
    if not meeting_file.exists():
        return result
    lines = meeting_file.read_text().split('\n')
    in_fm = False
    in_att = False
    for line in lines:
        if line.strip() == '---':
            if not in_fm:
                in_fm = True
                continue
            else:
                break
        if not in_fm:
            continue
        if line.startswith('start:'):
            result['start'] = line.split('start:', 1)[1].strip()
            in_att = False
        elif line.startswith('end:'):
            result['end'] = line.split('end:', 1)[1].strip()
            in_att = False
        elif line.startswith('gemini:'):
            result['gemini'] = line.split('gemini:', 1)[1].strip()
            in_att = False
        elif line.startswith('attendees:'):
            in_att = True
        elif in_att and line.startswith('  - '):
            attendee = line[4:].strip().strip('"').strip("'")
            result['attendees'].append(attendee)
        elif in_att and not line.startswith(' ') and line.strip():
            in_att = False
    return result


def build_meetings_table(meeting_rows: List[Tuple[str, str, Dict]]) -> str:
    """Build markdown table from sorted meeting rows."""
    lines = [
        '| Time | Meeting | Attendees | Summary |',
        '|------|---------|-----------|---------|',
    ]
    for _, stem, fm in meeting_rows:
        time_str = format_time_from_iso(fm.get('start', ''))
        display_title = re.sub(r'^\d{4}-\d{2}-\d{2} - ', '', stem)
        display_title_esc = display_title.replace('|', '\\|')
        stem_esc = stem.replace('|', '\\|')
        meeting_link = f'[[{stem_esc}\\|{display_title_esc}]]'
        attendees = fm.get('attendees', [])
        if len(attendees) > 6:
            att_str = ', '.join(a.replace('|', '\\|') for a in attendees[:6]) + ', ...'
        else:
            att_str = ', '.join(a.replace('|', '\\|') for a in attendees)
        gemini_url = fm.get('gemini', '')
        summary_cell = f'[🤖]({gemini_url})' if gemini_url else ''
        lines.append(f'| {time_str} | {meeting_link} | {att_str} | {summary_cell} |')
    return '\n'.join(lines)


def meeting_has_user_content(meeting_file: Path) -> bool:
    """Return True if the ## Actions section has any user content."""
    if not meeting_file.exists():
        return False
    content = meeting_file.read_text()
    if content.startswith('---'):
        end = content.find('---', 3)
        if end != -1:
            content = content[end + 3:]
    actions_match = re.search(
        r'^#+ Actions\s*\n(.*?)(?=\n---|\n#|\Z)',
        content, re.MULTILINE | re.DOTALL
    )
    if actions_match and actions_match.group(1).strip():
        return True
    return False


def update_daily_note(
    meeting_files: List[Tuple[str, Path]],
    vault_root: Path,
    target_date: datetime,
    valid_stems: Optional[Set[str]] = None,
    skill_base_dir: Optional[Path] = None,
) -> Set[str]:
    """Create/update the daily note meetings table.

    Returns the set of stems that were removed (stale/cancelled meetings).
    """
    year = target_date.strftime('%Y')
    month_num = target_date.strftime('%m')
    month_name = target_date.strftime('%B')
    day_name = target_date.strftime('%A')
    date_part = target_date.strftime('%Y-%m-%d')

    daily_dir = vault_root / 'DAILY_NOTES' / year / f'{month_num}-{month_name}'
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_note_file = daily_dir / f'{date_part} {day_name}.md'

    if daily_note_file.exists():
        content = daily_note_file.read_text()
    else:
        template_body = load_template(vault_root, 'Daily Note Template', skill_base_dir) or '# 📅 Meetings\n\n'
        created_dt = datetime.now().strftime('%Y-%m-%d %H:%M')
        content = f'---\ncreated: {created_dt}\ntags:\n  - Daily_Notes\n---\n\n{template_body}'

    meetings_dir = vault_root / 'MEETINGS'
    new_stems = {mf.stem for _, mf in meeting_files}

    if '# 📅 Meetings' in content:
        start_idx = content.find('# 📅 Meetings')
        end_idx = content.find('\n#', start_idx + 1)
        if end_idx == -1:
            end_idx = len(content)
        section_lines = content[start_idx:end_idx].split('\n')
        header = section_lines[0]
        existing_stems: Set[str] = set()
        before_table: List[str] = []
        after_table: List[str] = []
        in_content = False
        table_done = False
        for line in section_lines[1:]:
            stripped = line.strip()
            is_row = stripped.startswith('|')
            is_bullet = stripped.startswith('- [[')
            if (is_row or is_bullet) and not table_done:
                in_content = True
                if is_row and '[[' in stripped:
                    m = re.search(r'\[\[([^\\\]|]+)', stripped)
                    if m:
                        existing_stems.add(m.group(1).strip())
                elif is_bullet:
                    existing_stems.add(stripped[4:-2])
            elif in_content and not (is_row or is_bullet) and not table_done:
                table_done = True
                after_table.append(line)
            elif table_done:
                after_table.append(line)
            elif not in_content:
                before_table.append(line)

        all_stems = valid_stems if valid_stems is not None else (existing_stems | new_stems)
        stale_stems = existing_stems - all_stems

        meeting_rows: List[Tuple[str, str, Dict]] = []
        for stem in all_stems:
            matches = list(meetings_dir.rglob(f'{stem}.md'))
            fm = _read_meeting_fm(matches[0]) if matches else {'start': '', 'end': '', 'attendees': []}
            meeting_rows.append((fm.get('start', ''), stem, fm))
        meeting_rows.sort(key=lambda x: x[0])

        new_lines = [header]
        new_lines.extend(before_table if before_table else [''])
        new_lines.append(build_meetings_table(meeting_rows))
        new_lines.extend(after_table)
        new_section = '\n'.join(new_lines)
        content = content[:start_idx] + new_section + content[end_idx:]
    else:
        stale_stems = set()
        meeting_rows = []
        for _, mf in meeting_files:
            fm = _read_meeting_fm(mf)
            meeting_rows.append((fm.get('start', ''), mf.stem, fm))
        meeting_rows.sort(key=lambda x: x[0])
        section = '# 📅 Meetings\n\n' + build_meetings_table(meeting_rows)
        content = content.rstrip() + '\n\n' + section + '\n'

    daily_note_file.write_text(content)
    print(f'\n✓ Updated daily note: {daily_note_file.name}')
    return stale_stems


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def sync_to_vault(
    vault_root: Path,
    events: List[Dict],
    target_date: datetime,
    user_emails: Set[str],
    skill_base_dir: Optional[Path] = None,
) -> None:
    """Run the full sync: create meeting files, update daily note."""
    meeting_files: List[Tuple[str, Path]] = []
    valid_stems: Set[str] = set()
    skipped = 0
    target_date_str = target_date.strftime('%Y-%m-%d')

    for event in events:
        start = event.get('start', {})
        event_date = (start.get('dateTime') or start.get('date') or '')[:10]
        if event_date != target_date_str:
            skipped += 1
            continue

        summary = event.get('summary', '(no title)')
        try:
            expected = get_meeting_file_path(event, vault_root)
            valid_stems.add(expected.stem)
        except ValueError:
            pass

        print(f'\nProcessing: {summary}')
        try:
            result = create_or_update_meeting_note(event, vault_root, user_emails, skill_base_dir)
            if result:
                meeting_files.append(result)
        except Exception as e:
            print(f'  ✗ Error: {e}', file=sys.stderr)

    print(f'\n\nProcessed {len(meeting_files)} meetings (skipped {skipped} off-day events)')

    if meeting_files or (vault_root / 'DAILY_NOTES').exists():
        safe_valid = valid_stems if events else None
        stale_stems = update_daily_note(meeting_files, vault_root, target_date,
                                        valid_stems=safe_valid, skill_base_dir=skill_base_dir)
        if stale_stems:
            meetings_dir = vault_root / 'MEETINGS'
            print('\n⚠️  Cancelled meeting files to review:')
            for stem in sorted(stale_stems):
                matches = list(meetings_dir.rglob(f'{stem}.md'))
                if matches:
                    status = 'has user content in ## Actions' if meeting_has_user_content(matches[0]) else 'no user modifications'
                    print(f'  {matches[0].relative_to(vault_root)} ({status})')

    if meeting_files:
        print('\nChecking for Gemini transcripts...')
        for _, mf in meeting_files:
            update_meeting_with_gemini_notes(mf)
            capture_meeting_transcript(mf, vault_root)

    print('\n✅ Sync complete!')


def main():
    parser = argparse.ArgumentParser(description='Sync calendar events to Obsidian vault')
    parser.add_argument('--vault-root', required=True, help='Obsidian vault root path')
    parser.add_argument('--events-json', required=True, help='Path to discover_events.py output JSON')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: today)')
    parser.add_argument('--self-json', help='Path to discover_self.py output JSON (for user emails)')
    args = parser.parse_args()

    target_date = datetime.now()
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d')
        except ValueError:
            print(f'Error: Invalid date: {args.date}', file=sys.stderr)
            sys.exit(1)

    vault_root = Path(args.vault_root)
    if not vault_root.exists():
        print(f'Error: vault root does not exist: {vault_root}', file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.events_json) as f:
            events = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f'Error reading events JSON: {e}', file=sys.stderr)
        sys.exit(1)

    user_emails: Set[str] = set()
    if args.self_json:
        try:
            with open(args.self_json) as f:
                self_data = json.load(f)
            user_emails = {e.lower() for e in self_data.get('emails', [])}
        except (OSError, json.JSONDecodeError):
            pass

    skill_base_dir = Path(__file__).parent

    sync_to_vault(
        vault_root=vault_root,
        events=events,
        target_date=target_date,
        user_emails=user_emails,
        skill_base_dir=skill_base_dir,
    )


if __name__ == '__main__':
    main()
