#!/usr/bin/env python3
"""
Discover Obsidian vault configuration.

Uses obsidian-cli as primary method (when Obsidian is running), falls back
to reading .obsidian/ config files directly.

Output: JSON with vault_root, folder paths, date format, and today's file paths.

Usage:
    python3 discover_vault.py [--date YYYY-MM-DD] [--vault-path /override]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import moment→strftime conversion from co-located utility
sys.path.insert(0, str(Path(__file__).parent))
from obsidian_date_formatter import convert_moment_to_strftime


def _run(cmd: list, timeout: int = 10) -> Optional[str]:
    """Run a subprocess and return stdout, or None on failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _obsidian_running() -> bool:
    """Return True if the obsidian CLI is available and Obsidian is running."""
    out = _run(['obsidian', 'vault', 'info=name'])
    return out is not None and bool(out)


def _get_vault_path_from_cli() -> Optional[str]:
    """Get vault path via obsidian CLI."""
    return _run(['obsidian', 'vault', 'info=path'])


def _get_vault_path_from_file() -> Optional[str]:
    """Find vault path from Obsidian's application config file."""
    app_config = Path.home() / 'Library' / 'Application Support' / 'obsidian' / 'obsidian.json'
    if not app_config.exists():
        return None
    try:
        data = json.loads(app_config.read_text())
        vaults = data.get('vaults', {})
        if not vaults:
            return None
        # Prefer open vault, then most recently opened
        best = None
        best_ts = -1
        for vault_id, info in vaults.items():
            if info.get('open'):
                return info.get('path')
            ts = info.get('ts', 0)
            if ts > best_ts:
                best_ts = ts
                best = info.get('path')
        return best
    except (json.JSONDecodeError, Exception):
        return None


def _eval_obsidian(js_code: str) -> Optional[dict]:
    """Run obsidian eval and parse JSON result."""
    out = _run(['obsidian', 'eval', f'code={js_code}'])
    if out and out.startswith('=>'):
        out = out[2:].strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _get_daily_notes_config_from_cli() -> Optional[dict]:
    """Get live daily notes plugin config via obsidian eval."""
    return _eval_obsidian(
        "JSON.stringify(app.internalPlugins.getPluginById('daily-notes').instance.options)"
    )


def _get_templates_config_from_cli() -> Optional[dict]:
    """Get live templates plugin config via obsidian eval."""
    return _eval_obsidian(
        "JSON.stringify(app.internalPlugins.getPluginById('templates').instance.options)"
    )


def _get_daily_notes_config_from_file(vault_root: Path) -> dict:
    """Read daily notes config from .obsidian/daily-notes.json."""
    config_file = vault_root / '.obsidian' / 'daily-notes.json'
    defaults = {
        'folder': 'DAILY_NOTES',
        'format': 'YYYY/MM-MMMM/YYYY-MM-DD dddd',
        'template': 'TEMPLATES/Daily Note Template',
    }
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            return {**defaults, **data}
        except json.JSONDecodeError:
            pass
    return defaults


def _get_templates_config_from_file(vault_root: Path) -> dict:
    """Read templates config from .obsidian/templates.json."""
    config_file = vault_root / '.obsidian' / 'templates.json'
    defaults = {'folder': 'TEMPLATES'}
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            return {**defaults, **data}
        except json.JSONDecodeError:
            pass
    return defaults


def _parse_claude_md_folders(vault_root: Path) -> dict:
    """Extract folder conventions from CLAUDE.md."""
    claude_md = vault_root / 'CLAUDE.md'
    folders = {'meetings': 'MEETINGS', 'people': 'PEOPLE'}
    if not claude_md.exists():
        return folders
    content = claude_md.read_text()
    # Look for explicit folder mentions in bullet points
    import re
    meetings_match = re.search(r'\*\*MEETINGS/?\*\*|`MEETINGS/`|MEETINGS/', content)
    people_match = re.search(r'\*\*PEOPLE/?\*\*|`PEOPLE/`|PEOPLE/', content)
    if meetings_match:
        folders['meetings'] = 'MEETINGS'
    if people_match:
        folders['people'] = 'PEOPLE'
    return folders


def _compute_today_paths(vault_root: Path, daily_notes_config: dict, date: datetime) -> dict:
    """Compute today's daily note path and meetings directory."""
    moment_format = daily_notes_config.get('format', 'YYYY/MM-MMMM/YYYY-MM-DD dddd')
    strftime_format = convert_moment_to_strftime(moment_format)
    formatted = date.strftime(strftime_format)

    parts = formatted.split('/')
    if len(parts) > 1:
        subdir = '/'.join(parts[:-1])
        filename = parts[-1] + '.md'
    else:
        subdir = ''
        filename = formatted + '.md'

    folder = daily_notes_config.get('folder', 'DAILY_NOTES')
    if subdir:
        full_folder = f'{folder}/{subdir}'
    else:
        full_folder = folder

    daily_note_path = f'{full_folder}/{filename}'
    daily_note_absolute = str(vault_root / daily_note_path)

    year = date.strftime('%Y')
    month_num = date.strftime('%m')
    month_name = date.strftime('%B')
    meetings_dir = f'MEETINGS/{year}/{month_num}-{month_name}'

    return {
        'daily_note_path': daily_note_path,
        'daily_note_absolute': daily_note_absolute,
        'meetings_dir': meetings_dir,
    }


def discover_vault(vault_path_override: Optional[str] = None, date: Optional[datetime] = None) -> dict:
    """Discover Obsidian vault configuration.

    Returns a dict suitable for JSON serialization.
    """
    if date is None:
        date = datetime.now()

    obsidian_running = _obsidian_running()

    # 1. Get vault root
    vault_root_str = vault_path_override
    if not vault_root_str:
        if obsidian_running:
            vault_root_str = _get_vault_path_from_cli()
        if not vault_root_str:
            vault_root_str = _get_vault_path_from_file()
    if not vault_root_str:
        raise RuntimeError('Could not determine Obsidian vault path')

    vault_root = Path(vault_root_str)
    if not vault_root.exists():
        raise RuntimeError(f'Vault path does not exist: {vault_root}')

    # 2. Get vault name
    vault_name = vault_root.name

    # 3. Get daily notes config
    if obsidian_running:
        cli_config = _get_daily_notes_config_from_cli()
        daily_notes_config = cli_config if cli_config else _get_daily_notes_config_from_file(vault_root)
    else:
        daily_notes_config = _get_daily_notes_config_from_file(vault_root)

    # 4. Get templates config
    if obsidian_running:
        cli_tmpl = _get_templates_config_from_cli()
        templates_config = cli_tmpl if cli_tmpl else _get_templates_config_from_file(vault_root)
    else:
        templates_config = _get_templates_config_from_file(vault_root)

    # 5. Parse CLAUDE.md for folder conventions
    folders = _parse_claude_md_folders(vault_root)

    # 6. Compute today's paths
    today_paths = _compute_today_paths(vault_root, daily_notes_config, date)

    return {
        'vault_root': str(vault_root),
        'vault_name': vault_name,
        'obsidian_running': obsidian_running,
        'daily_notes': {
            'folder': daily_notes_config.get('folder', 'DAILY_NOTES'),
            'format': daily_notes_config.get('format', 'YYYY/MM-MMMM/YYYY-MM-DD dddd'),
            'template': daily_notes_config.get('template', 'TEMPLATES/Daily Note Template'),
        },
        'meetings': {'folder': folders['meetings']},
        'people': {'folder': folders['people']},
        'templates': {'folder': templates_config.get('folder', 'TEMPLATES')},
        'today': today_paths,
    }


def main():
    parser = argparse.ArgumentParser(description='Discover Obsidian vault configuration')
    parser.add_argument('--vault-path', help='Override vault root path')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: today)')
    args = parser.parse_args()

    date = datetime.now()
    if args.date:
        try:
            date = datetime.strptime(args.date, '%Y-%m-%d')
        except ValueError:
            print(f'Error: Invalid date format: {args.date}', file=sys.stderr)
            sys.exit(1)

    try:
        config = discover_vault(vault_path_override=args.vault_path, date=date)
        print(json.dumps(config, indent=2))
    except RuntimeError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
