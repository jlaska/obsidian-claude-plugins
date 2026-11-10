#!/usr/bin/env python3
"""User configuration for the daily-planner skill.

Company- and person-specific details (addresses to ignore, etc.) live in a config file,
not in the skill:

    $XDG_CONFIG_HOME/obsidian-productivity/daily-planner/config.yaml
    (default: ~/.config/obsidian-productivity/daily-planner/config.yaml)

Override the location with the ``DAILY_PLANNER_CONFIG`` environment variable.

A missing file means defaults. A broken file or pattern is reported on stderr and ignored
(the skill keeps running). Values in the file are merged over ``DEFAULTS``.

Supported keys:

    attendees:
      ignore_emails:        # exact addresses (case-insensitive)
        - infra-compute@example.com
      ignore_patterns:      # regular expressions, searched case-insensitively
        - '@group\\.calendar\\.google\\.com$'

PyYAML is used when importable. Otherwise a small built-in parser handles the subset
above (nested mappings, ``- item`` lists, inline ``[a, b]`` lists, quoted strings,
comments), because the skill scripts run under plain ``python3``.
"""

import copy
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_NAME = 'obsidian-productivity'
SKILL_NAME = 'daily-planner'

DEFAULTS: Dict[str, Any] = {
    'attendees': {
        'ignore_emails': [],
        'ignore_patterns': [],
    },
}


def config_path() -> Path:
    override = os.environ.get('DAILY_PLANNER_CONFIG')
    if override:
        return Path(override).expanduser()
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / PLUGIN_NAME / SKILL_NAME / 'config.yaml'


# ---------------------------------------------------------------------------
# Minimal YAML subset parser (fallback when PyYAML is unavailable)
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    out, quote = [], ''
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ''
        elif ch in '\'"':
            quote = ch
        elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            break
        out.append(ch)
    return ''.join(out).rstrip()


def _scalar(text: str) -> Any:
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in '\'"':
        inner = t[1:-1]
        return inner.replace("''", "'") if t[0] == "'" else \
            inner.replace('\\\\', '\x00').replace('\\"', '"').replace('\x00', '\\')
    if t.startswith('[') and t.endswith(']'):
        inner = t[1:-1].strip()
        return [_scalar(p) for p in _split_flow(inner)] if inner else []
    if t in ('~', 'null', 'Null', 'NULL', ''):
        return None
    if t in ('true', 'True'):
        return True
    if t in ('false', 'False'):
        return False
    if re.fullmatch(r'-?\d+', t):
        return int(t)
    return t


def _split_flow(text: str) -> List[str]:
    parts, cur, quote = [], [], ''
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ''
        elif ch in '\'"':
            quote = ch
            cur.append(ch)
        elif ch == ',':
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if ''.join(cur).strip():
        parts.append(''.join(cur))
    return parts


def parse_simple_yaml(text: str) -> Any:
    """Parse the YAML subset described in the module docstring. Raises ValueError."""
    lines: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if '\t' in raw[:len(raw) - len(raw.lstrip())]:
            raise ValueError('tabs are not allowed for indentation')
        s = _strip_comment(raw)
        if s.strip():
            lines.append((len(s) - len(s.lstrip(' ')), s.strip()))
    if not lines:
        return {}
    value, pos = _parse_block(lines, 0, lines[0][0])
    if pos != len(lines):
        raise ValueError(f'unexpected content: {lines[pos][1]!r}')
    return value


def _parse_block(lines: List[Tuple[int, str]], pos: int, indent: int) -> Tuple[Any, int]:
    if lines[pos][1].startswith('- ') or lines[pos][1] == '-':
        items: List[Any] = []
        while pos < len(lines) and lines[pos][0] == indent and (
                lines[pos][1].startswith('- ') or lines[pos][1] == '-'):
            items.append(_scalar(lines[pos][1][1:]))
            pos += 1
        return items, pos
    result: Dict[str, Any] = {}
    while pos < len(lines) and lines[pos][0] == indent:
        content = lines[pos][1]
        m = re.match(r'''^("[^"]*"|'[^']*'|[^:]+?)\s*:(?:\s+(.*))?$''', content)
        if not m:
            raise ValueError(f'cannot parse line: {content!r}')
        key = _scalar(m.group(1))
        rest = m.group(2)
        pos += 1
        if rest is not None and rest.strip():
            result[key] = _scalar(rest)
        elif pos < len(lines) and (lines[pos][0] > indent or (
                lines[pos][0] == indent and lines[pos][1].startswith('-'))):
            result[key], pos = _parse_block(lines, pos, lines[pos][0])
        else:
            result[key] = None
    if pos < len(lines) and lines[pos][0] > indent:
        raise ValueError(f'bad indentation near: {lines[pos][1]!r}')
    return result, pos


def _parse_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse_simple_yaml(text)
    return yaml.safe_load(text)


# ---------------------------------------------------------------------------
# Loading and use
# ---------------------------------------------------------------------------

def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        elif v is not None:
            base[k] = v
    return base


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return DEFAULTS merged with the user's config file. Never raises."""
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else config_path()
    if not p.is_file():
        return cfg
    try:
        data = _parse_yaml(p.read_text(encoding='utf-8'))
    except Exception as exc:
        print(f'⚠️  Ignoring invalid config {p}: {exc}', file=sys.stderr)
        return cfg
    if data is None:
        return cfg
    if not isinstance(data, dict):
        print(f'⚠️  Ignoring config {p}: top level must be a mapping', file=sys.stderr)
        return cfg
    _merge(cfg, data)
    att = cfg.get('attendees')
    if not isinstance(att, dict):
        print(f'⚠️  Config {p}: "attendees" must be a mapping; using defaults', file=sys.stderr)
        cfg['attendees'] = copy.deepcopy(DEFAULTS['attendees'])
    else:
        for key in ('ignore_emails', 'ignore_patterns'):
            val = att.get(key)
            if isinstance(val, str):
                val = [val]
            if not isinstance(val, list):
                print(f'⚠️  Config {p}: attendees.{key} must be a list; ignoring', file=sys.stderr)
                val = []
            att[key] = [str(v) for v in val if v is not None]
    return cfg


class AttendeeFilter:
    """Decides whether a calendar attendee address should be ignored."""

    def __init__(self, emails: List[str], patterns: List[str]):
        self.emails = {e.strip().lower() for e in emails if e and e.strip()}
        self.patterns = []
        for pat in patterns:
            try:
                self.patterns.append(re.compile(pat, re.I))
            except re.error as exc:
                print(f'⚠️  Ignoring invalid attendees.ignore_patterns entry {pat!r}: {exc}',
                      file=sys.stderr)

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]]) -> 'AttendeeFilter':
        att = (cfg or {}).get('attendees') or {}
        return cls(att.get('ignore_emails') or [], att.get('ignore_patterns') or [])

    def is_ignored(self, email: str) -> bool:
        e = (email or '').strip().lower()
        if not e:
            return False
        return e in self.emails or any(p.search(e) for p in self.patterns)


if __name__ == '__main__':
    import json
    print(json.dumps({'path': str(config_path()), 'exists': config_path().is_file(),
                      'config': load_config()}, indent=2))
