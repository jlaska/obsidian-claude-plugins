#!/usr/bin/env python3
"""
Obsidian Date Formatter

Reads Obsidian's date format configuration (moment.js format tokens) and outputs
correctly formatted paths for daily notes and meeting files.

Usage:
    python obsidian_date_formatter.py --vault-path /path/to/vault [--date YYYY-MM-DD]
    python obsidian_date_formatter.py --vault-path /path/to/vault --type daily
    python obsidian_date_formatter.py --vault-path /path/to/vault --type meeting --title "Meeting Title"
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


# Moment.js format tokens to Python strftime mappings
MOMENT_TO_STRFTIME = {
    'YYYY': '%Y',      # 4-digit year
    'YY': '%y',        # 2-digit year
    'MMMM': '%B',      # Full month name (January)
    'MMM': '%b',       # Short month name (Jan)
    'MM': '%m',        # 2-digit month (01-12)
    'M': '%-m',        # Month without leading zero (1-12)
    'DDDD': '%j',      # Day of year (001-366)
    'DD': '%d',        # 2-digit day (01-31)
    'D': '%-d',        # Day without leading zero (1-31)
    'dddd': '%A',      # Full day name (Monday)
    'ddd': '%a',       # Short day name (Mon)
    'dd': '%a',        # Short day name (Mon) - alternative
    'd': '%w',         # Day of week (0-6)
    'HH': '%H',        # Hour 24-hour (00-23)
    'H': '%-H',        # Hour 24-hour without leading zero (0-23)
    'hh': '%I',        # Hour 12-hour (01-12)
    'h': '%-I',        # Hour 12-hour without leading zero (1-12)
    'mm': '%M',        # Minutes (00-59)
    'm': '%-M',        # Minutes without leading zero (0-59)
    'ss': '%S',        # Seconds (00-59)
    's': '%-S',        # Seconds without leading zero (0-59)
    'A': '%p',         # AM/PM
    'a': '%p',         # am/pm
}


def convert_moment_to_strftime(moment_format: str) -> str:
    """
    Convert moment.js format string to Python strftime format.

    Args:
        moment_format: Moment.js format string (e.g., "YYYY/MM-MMMM/YYYY-MM-DD dddd")

    Returns:
        Python strftime format string
    """
    result = moment_format

    # Use placeholders to avoid re-replacement issues
    # Sort by length descending to replace longer tokens first (YYYY before YY)
    placeholder_map = {}
    placeholder_counter = 0

    for moment_token in sorted(MOMENT_TO_STRFTIME.keys(), key=len, reverse=True):
        if moment_token in result:
            # Use a placeholder that won't match any moment.js tokens
            placeholder = f"\x00{placeholder_counter}\x00"
            placeholder_map[placeholder] = MOMENT_TO_STRFTIME[moment_token]
            result = result.replace(moment_token, placeholder)
            placeholder_counter += 1

    # Replace all placeholders with actual strftime tokens
    for placeholder, strftime_token in placeholder_map.items():
        result = result.replace(placeholder, strftime_token)

    return result


def read_obsidian_config(vault_path: Path) -> dict:
    """
    Read Obsidian configuration files.

    Args:
        vault_path: Path to Obsidian vault root

    Returns:
        Dictionary with configuration data
    """
    config = {}

    # Read daily notes configuration
    daily_notes_config = vault_path / '.obsidian' / 'daily-notes.json'
    if daily_notes_config.exists():
        with open(daily_notes_config) as f:
            config['daily_notes'] = json.load(f)
    else:
        # Fallback to defaults from CLAUDE.md
        config['daily_notes'] = {
            'folder': 'DAILY_NOTES',
            'format': 'YYYY/MM-MMMM/YYYY-MM-DD dddd',
            'template': 'TEMPLATES/Daily Note Template'
        }

    # Read CLAUDE.md for meetings configuration
    claude_md = vault_path / 'CLAUDE.md'
    if claude_md.exists():
        # Default from CLAUDE.md conventions
        config['meetings'] = {
            'folder': 'MEETINGS',
            'format': 'YYYY/MM-MMMM'
        }
    else:
        config['meetings'] = {
            'folder': 'MEETINGS',
            'format': 'YYYY/MM-MMMM'
        }

    return config


def format_daily_note_path(vault_path: Path, date: datetime) -> dict:
    """
    Generate daily note path based on Obsidian configuration.

    Args:
        vault_path: Path to Obsidian vault root
        date: Date to format

    Returns:
        Dictionary with folder, filename, and full_path
    """
    config = read_obsidian_config(vault_path)
    daily_config = config['daily_notes']

    # Get the format string and convert to strftime
    moment_format = daily_config.get('format', 'YYYY/MM-MMMM/YYYY-MM-DD dddd')
    strftime_format = convert_moment_to_strftime(moment_format)

    # Format the date
    formatted = date.strftime(strftime_format)

    # Split into directory and filename
    # The format typically has directory parts before the last component
    parts = formatted.split('/')

    if len(parts) > 1:
        # Last part is filename, everything else is subdirectory
        subdir = '/'.join(parts[:-1])
        filename = parts[-1]
    else:
        # No subdirectory in format
        subdir = ''
        filename = formatted

    # Add .md extension if not present
    if not filename.endswith('.md'):
        filename += '.md'

    # Construct full path
    folder = daily_config.get('folder', 'DAILY_NOTES')
    if subdir:
        full_folder = f"{folder}/{subdir}"
    else:
        full_folder = folder

    full_path = f"{full_folder}/{filename}"

    return {
        'folder': folder,
        'subfolder': subdir,
        'full_folder': full_folder,
        'filename': filename,
        'full_path': full_path,
        'absolute_path': str(vault_path / full_path)
    }


def sanitize_meeting_title(title: str) -> str:
    """
    Sanitize meeting title for filesystem.

    Args:
        title: Raw meeting title

    Returns:
        Sanitized title safe for filenames
    """
    # Replace problematic characters
    sanitized = title.replace('/', '-').replace(':', '-').replace('|', '-')
    sanitized = sanitized.replace('  ', ' ').strip()
    return sanitized


def format_meeting_path(vault_path: Path, date: datetime, title: str) -> dict:
    """
    Generate meeting file path based on Obsidian configuration.

    Args:
        vault_path: Path to Obsidian vault root
        date: Meeting date
        title: Meeting title

    Returns:
        Dictionary with folder, filename, and full_path
    """
    config = read_obsidian_config(vault_path)
    meeting_config = config['meetings']

    # Get the format string and convert to strftime
    moment_format = meeting_config.get('format', 'YYYY/MM-MMMM')
    strftime_format = convert_moment_to_strftime(moment_format)

    # Format the date for directory
    subdir = date.strftime(strftime_format)

    # Sanitize title and create filename
    sanitized_title = sanitize_meeting_title(title)
    filename = f"{date.strftime('%Y-%m-%d')} - {sanitized_title}.md"

    # Construct full path
    folder = meeting_config.get('folder', 'MEETINGS')
    full_folder = f"{folder}/{subdir}"
    full_path = f"{full_folder}/{filename}"

    return {
        'folder': folder,
        'subfolder': subdir,
        'full_folder': full_folder,
        'filename': filename,
        'full_path': full_path,
        'absolute_path': str(vault_path / full_path)
    }


def main():
    parser = argparse.ArgumentParser(
        description='Format Obsidian dates based on vault configuration'
    )
    parser.add_argument(
        '--vault-path',
        required=True,
        type=Path,
        help='Path to Obsidian vault root'
    )
    parser.add_argument(
        '--date',
        type=str,
        help='Date in YYYY-MM-DD format (default: today)'
    )
    parser.add_argument(
        '--type',
        choices=['daily', 'meeting'],
        default='daily',
        help='Type of path to generate (default: daily)'
    )
    parser.add_argument(
        '--title',
        type=str,
        help='Meeting title (required for --type meeting)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )

    args = parser.parse_args()

    # Parse date
    if args.date:
        try:
            date = datetime.strptime(args.date, '%Y-%m-%d')
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        date = datetime.now()

    # Validate vault path
    if not args.vault_path.exists():
        print(f"Error: Vault path does not exist: {args.vault_path}", file=sys.stderr)
        sys.exit(1)

    obsidian_dir = args.vault_path / '.obsidian'
    if not obsidian_dir.exists():
        print(f"Warning: .obsidian directory not found in {args.vault_path}", file=sys.stderr)

    # Generate path based on type
    if args.type == 'daily':
        result = format_daily_note_path(args.vault_path, date)
    elif args.type == 'meeting':
        if not args.title:
            print("Error: --title is required for --type meeting", file=sys.stderr)
            sys.exit(1)
        result = format_meeting_path(args.vault_path, date, args.title)

    # Output result
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result['full_path'])

    return 0


if __name__ == '__main__':
    sys.exit(main())
