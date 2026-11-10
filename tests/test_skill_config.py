"""Tests for skills/daily-planner/skill_config.py."""
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / 'skills' / 'daily-planner'
sys.path.insert(0, str(SKILL))
import skill_config as sc  # noqa: E402
import sync_to_vault as stv  # noqa: E402

EXAMPLE = (SKILL / 'config.example.yaml').read_text()


class TestPath:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv('DAILY_PLANNER_CONFIG', raising=False)
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
        assert sc.config_path() == Path.home() / '.config/obsidian-productivity/daily-planner/config.yaml'

    def test_xdg_and_override(self, monkeypatch, tmp_path):
        monkeypatch.delenv('DAILY_PLANNER_CONFIG', raising=False)
        monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
        assert sc.config_path() == tmp_path / 'obsidian-productivity/daily-planner/config.yaml'
        monkeypatch.setenv('DAILY_PLANNER_CONFIG', str(tmp_path / 'x.yaml'))
        assert sc.config_path() == tmp_path / 'x.yaml'


class TestParser:
    def test_example_parses_with_builtin_parser(self):
        data = sc.parse_simple_yaml(EXAMPLE)
        assert data['attendees']['ignore_emails'] == ['team-list@example.com']
        assert data['attendees']['ignore_patterns'][0] == r'@group\.calendar\.google\.com$'
        assert len(data['attendees']['ignore_patterns']) == 3

    def test_builtin_matches_pyyaml(self):
        yaml = pytest.importorskip('yaml')
        assert sc.parse_simple_yaml(EXAMPLE) == yaml.safe_load(EXAMPLE)

    def test_inline_lists_quotes_comments(self):
        data = sc.parse_simple_yaml(
            "attendees:\n  ignore_emails: [a@x.com, 'b@x.com']  # note\n"
            "  ignore_patterns:\n    - \"c#d\"\n")
        assert data['attendees']['ignore_emails'] == ['a@x.com', 'b@x.com']
        assert data['attendees']['ignore_patterns'] == ['c#d']

    @pytest.mark.parametrize('bad', ['attendees:\n\t- x\n', 'just a line', 'a:\n  b: 1\n c: 2\n'])
    def test_bad_input_raises(self, bad):
        with pytest.raises(ValueError):
            sc.parse_simple_yaml(bad)


class TestLoad:
    def test_missing_file_gives_defaults(self, tmp_path):
        assert sc.load_config(tmp_path / 'nope.yaml') == sc.DEFAULTS

    def test_merges_user_values(self, tmp_path):
        f = tmp_path / 'c.yaml'
        f.write_text(EXAMPLE)
        cfg = sc.load_config(f)
        assert 'team-list@example.com' in cfg['attendees']['ignore_emails']

    def test_invalid_file_warns_and_returns_defaults(self, tmp_path, capsys):
        f = tmp_path / 'c.yaml'
        f.write_text('attendees:\n\t- x\n')
        assert sc.load_config(f) == sc.DEFAULTS
        assert 'Ignoring invalid config' in capsys.readouterr().err

    def test_string_instead_of_list_is_accepted(self, tmp_path):
        f = tmp_path / 'c.yaml'
        f.write_text('attendees:\n  ignore_emails: a@x.com\n')
        assert sc.load_config(f)['attendees']['ignore_emails'] == ['a@x.com']

    def test_defaults_not_mutated(self, tmp_path):
        f = tmp_path / 'c.yaml'
        f.write_text(EXAMPLE)
        sc.load_config(f)
        assert sc.DEFAULTS['attendees']['ignore_emails'] == []


class TestAttendeeFilter:
    def _filter(self, tmp_path):
        f = tmp_path / 'c.yaml'
        f.write_text(EXAMPLE)
        return sc.AttendeeFilter.from_config(sc.load_config(f))

    def test_exact_and_pattern(self, tmp_path):
        flt = self._filter(tmp_path)
        assert flt.is_ignored('Team-List@Example.com')
        assert flt.is_ignored('c_abc123@group.calendar.google.com')
        assert flt.is_ignored('room@resource.calendar.google.com')
        assert not flt.is_ignored('joel.low@example.com')
        assert not flt.is_ignored('')

    def test_invalid_regex_is_skipped_with_warning(self, capsys):
        flt = sc.AttendeeFilter([], ['(unclosed', r'@ok\.com$'])
        assert flt.is_ignored('a@ok.com')
        assert 'invalid' in capsys.readouterr().err

    def test_empty_config_ignores_nothing(self):
        assert not sc.AttendeeFilter.from_config(None).is_ignored('a@b.com')


class TestSyncUsesFilter:
    GROUP = 'c_a45fe75f9ffa296fb8777199655723516866789567b1e3238a5ed2414809c247@group.calendar.google.com'

    def _event(self):
        return {
            'id': 'e1', 'summary': 'Weekly Sync',
            'start': {'dateTime': '2026-11-10T07:30:00-05:00'},
            'end': {'dateTime': '2026-11-10T08:20:00-05:00'},
            'attendees': [
                {'email': self.GROUP, 'displayName': 'Public Meetings'},
                {'email': 'infra-compute@example.com', 'displayName': 'Infra Compute'},
                {'email': 'joel.low@example.com', 'organizer': True},
                {'email': 'me@example.com', 'self': True},
            ],
        }

    def _vault(self, tmp_path):
        v = tmp_path / 'vault'
        for d in ('MEETINGS', 'PEOPLE', 'TEMPLATES', 'DAILY_NOTES'):
            (v / d).mkdir(parents=True)
        return v

    def _run(self, tmp_path, flt):
        v = self._vault(tmp_path)
        res = stv.create_or_update_meeting_note(self._event(), v, {'me@example.com'}, SKILL, flt)
        assert res
        return res[1].read_text()

    def test_filtered_attendees_are_not_written(self, tmp_path):
        flt = sc.AttendeeFilter(['infra-compute@example.com'], [r'@group\.calendar\.google\.com$'])
        text = self._run(tmp_path, flt)
        assert 'Joel Low' in text
        assert 'Infra Compute' not in text and 'C A45fe' not in text and 'Public Meetings' not in text

    def test_without_filter_nothing_is_dropped(self, tmp_path):
        text = self._run(tmp_path, None)
        assert 'Infra Compute' in text
