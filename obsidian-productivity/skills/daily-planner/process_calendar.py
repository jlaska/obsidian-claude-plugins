#!/usr/bin/env python3
"""
Process Google Calendar events and create Obsidian meeting notes and daily note.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set


class ObsidianDailyPlanner:
    def __init__(self, vault_root: str):
        self.vault_root = Path(vault_root)
        self.people_dir = self.vault_root / "PEOPLE"
        self.meetings_dir = self.vault_root / "MEETINGS"
        self.daily_notes_dir = self.vault_root / "DAILY_NOTES"

        # Load all people files and their emails
        self.people_map = self._load_people_map()

    def _load_people_map(self) -> Dict[str, str]:
        """Load mapping of emails to person filenames."""
        people_map = {}

        for person_file in self.people_dir.glob("*.md"):
            name = person_file.stem  # Filename without .md

            # Read frontmatter to get email
            try:
                with open(person_file, 'r') as f:
                    content = f.read()
                    # Extract email from frontmatter
                    email_match = re.search(r'^email:\s*(.+)$', content, re.MULTILINE)
                    if email_match:
                        email = email_match.group(1).strip()
                        if email:
                            people_map[email] = name

                    # Also map by name for fallback matching
                    people_map[name.lower()] = name
            except Exception as e:
                print(f"Warning: Could not read {person_file}: {e}", file=sys.stderr)

        return people_map

    def _match_attendee(self, attendee: Dict) -> tuple[str, bool]:
        """
        Match calendar attendee to a person file.

        Returns:
            tuple[str, bool]: (name, is_people_match) where:
                - name: The name to display (never empty)
                - is_people_match: True if matched to People file, False otherwise
        """
        email = attendee.get('email', '')
        display_name = attendee.get('displayName', '')

        # First try exact email match
        if email in self.people_map:
            return (self.people_map[email], True)

        # Try matching by name
        if display_name:
            name_lower = display_name.lower()
            if name_lower in self.people_map:
                return (self.people_map[name_lower], True)

        # Use gog people search for fallback
        gog_name = None
        if email:
            try:
                import subprocess
                result = subprocess.run(
                    ['gog', 'people', 'search', '--json', email],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    people = data.get('people', [])
                    if people and len(people) > 0:
                        person = people[0]
                        full_name = person.get('name', '').strip()
                        if full_name:
                            # Check if this person exists in our vault
                            if full_name.lower() in self.people_map:
                                return (self.people_map[full_name.lower()], True)
                            # Store gog name for fallback
                            gog_name = full_name
            except Exception as e:
                print(f"Warning: Could not search for {email}: {e}", file=sys.stderr)

        # Return fallbacks in priority order (all non-People matches)
        if gog_name:
            return (gog_name, False)
        if display_name:
            return (display_name, False)
        # Extract username from email as last resort
        if email:
            email_username = email.split('@')[0]
            return (email_username, False)

        # This should never happen, but just in case
        return ("Unknown", False)

    def _sanitize_filename(self, title: str) -> str:
        """Sanitize meeting title for filename."""
        # Replace problematic characters
        title = title.replace('/', ' - ')
        title = title.replace(':', ' - ')
        title = title.replace('|', ' - ')
        title = re.sub(r'\s+', ' ', title)  # Collapse multiple spaces
        title = title.strip()
        return title

    def _classify_attachment(self, attachment: Dict) -> tuple[str, str]:
        """Classify attachment and return (property_name, url)."""
        title = attachment.get('title', '').lower()
        url = attachment.get('fileUrl', '')

        # Gemini notes
        if 'gemini' in title or 'notes by gemini' in title:
            if 'transcript' in title:
                return ('transcript', url)
            else:
                return ('agenda', url)

        # Meeting recordings
        if 'recording' in title:
            return ('recording', url)

        # Google Docs - check title for keywords
        if 'notes' in title or 'agenda' in title or '1:1' in title or '1-1' in title:
            return ('agenda', url)

        if 'minutes' in title or 'summary' in title or 'recap' in title:
            return ('minutes', url)

        # Default to agenda for Google Docs
        return ('agenda', url)

    def _create_meeting_note(self, event: Dict, date_str: str) -> Optional[str]:
        """Create a meeting note file and return the wikilink."""
        # Skip if not a real meeting
        event_type = event.get('eventType', 'default')
        if event_type == 'workingLocation':
            return None

        # Skip if declined
        attendees = event.get('attendees', [])
        for attendee in attendees:
            if attendee.get('self') and attendee.get('responseStatus') == 'declined':
                return None

        # Skip if no attendees (personal time block)
        if not attendees:
            return None

        # Skip if only attendee is self (personal event or broadcast with no interaction)
        non_self_attendees = [a for a in attendees if not a.get('self')]
        if len(non_self_attendees) == 0:
            return None

        # Skip broadcast events (guestsCanSeeOtherGuests: false usually indicates this)
        if event.get('guestsCanSeeOtherGuests') is False and event.get('guestsCanInviteOthers') is False:
            return None

        summary = event.get('summary', 'Untitled Meeting')
        sanitized_title = self._sanitize_filename(summary)

        # Determine date path
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        year = date_obj.strftime('%Y')
        month = date_obj.strftime('%m-%B')

        # Create meeting file path
        meeting_dir = self.meetings_dir / year / month
        meeting_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{date_str} - {sanitized_title}.md"
        meeting_file = meeting_dir / filename

        # Check if file already exists
        if meeting_file.exists():
            print(f"Meeting note already exists: {filename}", file=sys.stderr)
            return f"[[{date_str} - {sanitized_title}]]"

        # Match attendees to people
        matched_attendees = []
        for attendee in attendees:
            if attendee.get('self'):
                continue  # Skip self

            name, is_people_match = self._match_attendee(attendee)
            if is_people_match:
                matched_attendees.append(f'"[[{name}]]"')
            else:
                matched_attendees.append(f'"{name}"')

        # Build frontmatter
        frontmatter = ["---"]

        if matched_attendees:
            frontmatter.append("attendees:")
            for attendee in matched_attendees:
                frontmatter.append(f"  - {attendee}")
        else:
            frontmatter.append("attendees:")

        frontmatter.append("tags:")
        frontmatter.append("  - Meetings")

        # Add created timestamp
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        frontmatter.append(f"created: {now}")

        # Add start/end times if available
        start = event.get('start', {})
        end = event.get('end', {})
        if 'dateTime' in start:
            frontmatter.append(f"start: {start['dateTime']}")
        if 'dateTime' in end:
            frontmatter.append(f"end: {end['dateTime']}")

        # Add Google Meet link
        hangout_link = event.get('hangoutLink')
        if hangout_link:
            frontmatter.append(f"gmeet: {hangout_link}")

        # Add URL
        html_link = event.get('htmlLink')
        if html_link:
            frontmatter.append(f"URL: {html_link}")

        # Process attachments
        attachments = event.get('attachments', [])
        attachment_properties = {}
        for attachment in attachments:
            prop_name, url = self._classify_attachment(attachment)
            if prop_name not in attachment_properties:
                attachment_properties[prop_name] = []
            attachment_properties[prop_name].append(url)

        # Add attachment properties
        for prop_name, urls in attachment_properties.items():
            if len(urls) == 1:
                frontmatter.append(f"{prop_name}: {urls[0]}")
            else:
                frontmatter.append(f"{prop_name}:")
                for url in urls:
                    frontmatter.append(f"  - {url}")

        frontmatter.append("---")

        # Build body
        body = []
        body.append("## Actions")
        body.append("")
        body.append("## Agenda")

        # Add description if available
        description = event.get('description')
        if description:
            # Strip HTML tags for plain text
            description = re.sub(r'<[^>]+>', '', description)
            description = description.strip()
            if description:
                body.append("")
                body.append(description)

        body.append("")
        body.append("## Recent Meetings")
        body.append("")
        body.append("```base")
        body.append("views:")
        body.append("  - type: table")
        body.append("    name: Table")
        body.append("    filters:")
        body.append("      and:")
        body.append('        - file.tags.contains("Meetings")')
        body.append("        - list(attendees).containsAny(this.attendees)")
        body.append("    order:")
        body.append("      - file.name")
        body.append("      - attendees")
        body.append("    sort:")
        body.append("      - property: file.name")
        body.append("        direction: DESC")
        body.append("      - property: file.ctime")
        body.append("        direction: DESC")
        body.append("    limit: 10")
        body.append("```")

        # Write file
        content = "\n".join(frontmatter) + "\n" + "\n".join(body) + "\n"
        with open(meeting_file, 'w') as f:
            f.write(content)

        print(f"Created meeting note: {filename}")
        return f"[[{date_str} - {sanitized_title}]]"

    def _get_meeting_start_time(self, wikilink: str) -> str:
        """Extract start time from meeting file frontmatter."""
        # Extract filename from wikilink (format: [[filename]])
        filename_match = re.match(r'\[\[(.*?)\]\]', wikilink)
        if not filename_match:
            return ''

        filename = filename_match.group(1)

        # Search for the meeting file
        for meeting_file in self.meetings_dir.rglob(f"{filename}.md"):
            try:
                with open(meeting_file, 'r') as f:
                    content = f.read()
                    # Extract start time from frontmatter
                    start_match = re.search(r'^start:\s*(.+)$', content, re.MULTILINE)
                    if start_match:
                        return start_match.group(1).strip()
            except Exception as e:
                print(f"Warning: Could not read {meeting_file}: {e}", file=sys.stderr)

        return ''

    def create_daily_note(self, date_str: str, meeting_entries: List[tuple[str, str]]):
        """Create or update daily note with meeting entries (start_time, wikilink tuples)."""
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        year = date_obj.strftime('%Y')
        month = date_obj.strftime('%m-%B')
        day_name = date_obj.strftime('%A')

        # Create daily note directory
        daily_dir = self.daily_notes_dir / year / month
        daily_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{date_str} {day_name}.md"
        daily_file = daily_dir / filename

        # Check if file already exists
        if daily_file.exists():
            # Update existing file
            with open(daily_file, 'r') as f:
                content = f.read()

            # Find the # 📅 Meetings section
            meetings_section_match = re.search(r'^# 📅 Meetings\s*\n((?:- \[\[.*?\]\]\n?)*)', content, re.MULTILINE)

            if meetings_section_match:
                # Extract existing links
                existing_links_text = meetings_section_match.group(1)
                existing_links = re.findall(r'- (\[\[.*?\]\])', existing_links_text)

                # Get start times for existing links
                existing_entries = []
                for link in existing_links:
                    start_time = self._get_meeting_start_time(link)
                    existing_entries.append((start_time, link))

                # Merge with new entries
                all_entries = existing_entries + meeting_entries

                # Remove duplicates (keep unique wikilinks)
                seen_links = set()
                unique_entries = []
                for start_time, link in all_entries:
                    if link not in seen_links:
                        seen_links.add(link)
                        unique_entries.append((start_time, link))

                # Sort by start time
                unique_entries.sort(key=lambda x: x[0] if x[0] else '')

                # Format as list items
                new_links_text = "\n".join([f"- {link}" for _, link in unique_entries])

                # Replace the section
                new_content = content[:meetings_section_match.start(1)] + new_links_text + "\n" + content[meetings_section_match.end(1):]

                with open(daily_file, 'w') as f:
                    f.write(new_content)

                print(f"Updated daily note: {filename}")
            else:
                # Add meetings section if it doesn't exist
                # Sort entries by start time
                sorted_entries = sorted(meeting_entries, key=lambda x: x[0] if x[0] else '')
                meetings_text = "\n# 📅 Meetings\n\n" + "\n".join([f"- {link}" for _, link in sorted_entries]) + "\n"

                with open(daily_file, 'a') as f:
                    f.write(meetings_text)

                print(f"Added meetings section to daily note: {filename}")
        else:
            # Create new daily note from template
            now = datetime.now().strftime('%Y-%m-%d %H:%M')

            content = f"""---
created: {now}
tags:
  - Daily_Notes
---
---
### 📓 Daily Journal Prompts
#### 🌜 Last night, after work, I...
-
#### 🙌 One thing I've excited about right now is...
-
#### 👎 One thing I'm struggling with today is...
-
#### 📢 How will you be a good leader today?
-
#### 5️⃣ Words that describe your current state?
-

---
### 🎯 Objectives
#### 🔲 What are the new tasks?
*
#### 🔲 What must be completed today?
```tasks
not done
short mode
(due today)
group by priority
```
#### 🔲 What must be completed this week?

```tasks
not done
short mode
due in this week
group by due
sort by priority
```

#### 🔲 What must be completed eventually?
```tasks
not done
short mode
(no due date)
group by priority
```

#### ☑️ List of things you did complete
```tasks
done
short mode
done today
```


---
# 📅 Meetings

"""

            # Add meeting links (already sorted by start time)
            for _, link in meeting_entries:
                content += f"- {link}\n"

            with open(daily_file, 'w') as f:
                f.write(content)

            print(f"Created daily note: {filename}")

    def process_calendar_events(self, events_json_path: str, date_str: str):
        """Process calendar events and create meeting notes and daily note."""
        with open(events_json_path, 'r') as f:
            data = json.load(f)

        events = data.get('events', [])

        # Sort events by start time for chronological order
        # All-day events (with 'date' instead of 'dateTime') sort to beginning of day
        def get_sort_key(event):
            start = event.get('start', {})
            if 'dateTime' in start:
                return start['dateTime']
            elif 'date' in start:
                return start['date'] + 'T00:00:00'  # Sort to beginning of day
            return ''

        events.sort(key=get_sort_key)

        # Collect meeting entries as (start_time, wikilink) tuples
        meeting_entries = []

        for event in events:
            wikilink = self._create_meeting_note(event, date_str)
            if wikilink:
                start_time = event.get('start', {}).get('dateTime', '')
                meeting_entries.append((start_time, wikilink))

        # Create or update daily note
        if meeting_entries:
            self.create_daily_note(date_str, meeting_entries)
        else:
            print("No meetings to add to daily note.")


def main():
    if len(sys.argv) < 3:
        print("Usage: process_calendar.py <vault_root> <events_json_path> [date]")
        sys.exit(1)

    vault_root = sys.argv[1]
    events_json_path = sys.argv[2]
    date_str = sys.argv[3] if len(sys.argv) > 3 else datetime.now().strftime('%Y-%m-%d')

    planner = ObsidianDailyPlanner(vault_root)
    planner.process_calendar_events(events_json_path, date_str)


if __name__ == '__main__':
    main()
