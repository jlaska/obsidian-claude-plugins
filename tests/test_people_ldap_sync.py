"""Tests for people-ldap-sync social URL support.

Tests cover LDIF parsing of rhatSocialURL, frontmatter merge logic,
YAML serialization, dry-run mode, and end-to-end enrichment.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SKILL = Path(__file__).parent.parent / "skills" / "people-ldap-sync"
sys.path.insert(0, str(_SKILL))

from enrich_people import PersonEnricher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_person_file(tmp_path, name, frontmatter_yaml, body="# Bio\n"):
    """Create a markdown file with the given frontmatter YAML string."""
    content = f"---\n{frontmatter_yaml}---\n{body}"
    path = tmp_path / f"{name}.md"
    path.write_text(content)
    return path


def _read_frontmatter(path):
    """Read a markdown file and return its parsed frontmatter dict."""
    content = path.read_text()
    enricher = PersonEnricher(path.parent)
    fm, _ = enricher.parse_frontmatter(content)
    return fm


def _mock_ldap_result(ldif_text):
    """Return a MagicMock that simulates a successful ldapsearch subprocess result."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = ldif_text
    r.stderr = ""
    return r


# ---------------------------------------------------------------------------
# LDIF constants
# ---------------------------------------------------------------------------

LDIF_SINGLE_SOCIAL = """\
dn: uid=jdoe,ou=users,dc=redhat,dc=com
uid: jdoe
title: Senior Engineer
mail: jdoe@redhat.com
rhatSocialURL: https://github.com/jdoe

"""

LDIF_MULTIPLE_SOCIAL = """\
dn: uid=jdoe,ou=users,dc=redhat,dc=com
uid: jdoe
title: Senior Engineer
mail: jdoe@redhat.com
rhatSocialURL: https://github.com/jdoe
rhatSocialURL: https://gitlab.cee.redhat.com/jdoe
rhatSocialURL: https://twitter.com/jdoe

"""

LDIF_NO_SOCIAL = """\
dn: uid=jdoe,ou=users,dc=redhat,dc=com
uid: jdoe
title: Senior Engineer
mail: jdoe@redhat.com
rhatLocation: RH - Raleigh

"""

LDIF_MIXED_FIELDS = """\
dn: uid=jdoe,ou=users,dc=redhat,dc=com
uid: jdoe
title: Principal Software Engineer
rhatLocation: Remote US NC
mail: jdoe@redhat.com
mobile: +19195551234
rhatSocialURL: https://github.com/jdoe
rhatSocialURL: https://gitlab.cee.redhat.com/jdoe

"""


# ===========================================================================
# TestLdapSocialParsing
# ===========================================================================


class TestLdapSocialParsing:
    """Test get_ldap_data() LDIF parsing of rhatSocialURL."""

    def setup_method(self):
        self.enricher = PersonEnricher(Path("/tmp/fake"))

    def test_single_social_url(self):
        with patch("enrich_people.subprocess.run", return_value=_mock_ldap_result(LDIF_SINGLE_SOCIAL)):
            data = self.enricher.get_ldap_data("jdoe@redhat.com")
        assert data is not None
        assert data["uid"] == "jdoe"
        assert data["social"] == ["https://github.com/jdoe"]

    def test_multiple_social_urls(self):
        with patch("enrich_people.subprocess.run", return_value=_mock_ldap_result(LDIF_MULTIPLE_SOCIAL)):
            data = self.enricher.get_ldap_data("jdoe@redhat.com")
        assert data is not None
        assert data["uid"] == "jdoe"
        assert data["social"] == [
            "https://github.com/jdoe",
            "https://gitlab.cee.redhat.com/jdoe",
            "https://twitter.com/jdoe",
        ]

    def test_no_social_url(self):
        with patch("enrich_people.subprocess.run", return_value=_mock_ldap_result(LDIF_NO_SOCIAL)):
            data = self.enricher.get_ldap_data("jdoe@redhat.com")
        assert data is not None
        assert data["uid"] == "jdoe"
        assert "social" not in data

    def test_mixed_fields(self):
        with patch("enrich_people.subprocess.run", return_value=_mock_ldap_result(LDIF_MIXED_FIELDS)):
            data = self.enricher.get_ldap_data("jdoe@redhat.com")
        assert data is not None
        assert data["uid"] == "jdoe"
        assert data["title"] == "Principal Software Engineer"
        assert data["rhatLocation"] == "Remote US NC"
        assert data["mail"] == "jdoe@redhat.com"
        assert data["mobile"] == "+19195551234"
        assert isinstance(data["social"], list)
        assert data["social"] == [
            "https://github.com/jdoe",
            "https://gitlab.cee.redhat.com/jdoe",
        ]


# ===========================================================================
# TestSocialMerge
# ===========================================================================


class TestSocialMerge:
    """Test frontmatter social merge logic in enrich_person_file()."""

    LDAP_WITH_SOCIAL = {
        "title": "Engineer",
        "mail": "jdoe@redhat.com",
        "social": ["https://github.com/jdoe", "https://twitter.com/jdoe"],
    }

    LDAP_NO_SOCIAL = {
        "title": "Engineer",
        "mail": "jdoe@redhat.com",
    }

    LDAP_WITH_UID = {
        "uid": "jdoe",
        "title": "Engineer",
        "mail": "jdoe@redhat.com",
    }

    LDAP_WITH_UID_AND_SOCIAL = {
        "uid": "jdoe",
        "title": "Engineer",
        "mail": "jdoe@redhat.com",
        "social": ["https://github.com/jdoe"],
    }

    LDAP_WITH_UID_AND_GITLAB_SOCIAL = {
        "uid": "jdoe",
        "title": "Engineer",
        "mail": "jdoe@redhat.com",
        "social": ["https://github.com/jdoe", "https://gitlab.cee.redhat.com/jdoe"],
    }

    def _enrich(self, tmp_path, frontmatter_yaml, ldap_data):
        """Create a file, mock LDAP, run enrichment, return (result_bool, parsed_frontmatter)."""
        path = _make_person_file(tmp_path, "Jane Doe", frontmatter_yaml)
        enricher = PersonEnricher(tmp_path)
        with patch.object(enricher, "get_ldap_data", return_value=ldap_data):
            with patch.object(enricher, "get_email_from_gog", return_value=None):
                result = enricher.enrich_person_file(path)
        fm = _read_frontmatter(path)
        return result, fm, enricher

    def test_no_existing_social_adds_from_ldap(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_SOCIAL)
        assert result is True
        assert fm["social"] == ["https://github.com/jdoe", "https://twitter.com/jdoe"]

    def test_existing_social_appends_new(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial:\n  - https://github.com/jdoe\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_SOCIAL)
        assert result is True
        assert fm["social"] == ["https://github.com/jdoe", "https://twitter.com/jdoe"]

    def test_duplicate_urls_not_added(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial:\n  - https://github.com/jdoe\n  - https://twitter.com/jdoe\ntags:\n  - People\ntitle: Engineer\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_SOCIAL)
        assert result is False
        assert fm["social"] == ["https://github.com/jdoe", "https://twitter.com/jdoe"]

    def test_string_social_normalized_to_list(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial: https://github.com/jdoe\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_SOCIAL)
        assert result is True
        assert isinstance(fm["social"], list)
        assert "https://github.com/jdoe" in fm["social"]
        assert "https://twitter.com/jdoe" in fm["social"]

    def test_none_social_treated_as_empty(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial:\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_SOCIAL)
        assert result is True
        assert fm["social"] == ["https://github.com/jdoe", "https://twitter.com/jdoe"]

    def test_no_ldap_social_preserves_existing(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial:\n  - https://github.com/jdoe\ntags:\n  - People\ntitle: Engineer\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_NO_SOCIAL)
        assert fm["social"] == ["https://github.com/jdoe"]

    def test_manual_urls_preserved(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial:\n  - https://personal-blog.com/jdoe\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_SOCIAL)
        assert result is True
        assert "https://personal-blog.com/jdoe" in fm["social"]
        assert "https://github.com/jdoe" in fm["social"]
        assert "https://twitter.com/jdoe" in fm["social"]

    def test_uid_constructs_gitlab_url(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_UID)
        assert result is True
        assert "https://gitlab.cee.redhat.com/jdoe" in fm["social"]

    def test_uid_gitlab_url_appended_to_existing_social(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\ntags:\n  - People\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_UID_AND_SOCIAL)
        assert result is True
        assert fm["social"] == [
            "https://github.com/jdoe",
            "https://gitlab.cee.redhat.com/jdoe",
        ]

    def test_uid_gitlab_url_not_duplicated(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\nsocial:\n  - https://gitlab.cee.redhat.com/jdoe\ntags:\n  - People\ntitle: Engineer\n"
        result, fm, _ = self._enrich(tmp_path, fm_yaml, self.LDAP_WITH_UID_AND_GITLAB_SOCIAL)
        assert fm["social"].count("https://gitlab.cee.redhat.com/jdoe") == 1


# ===========================================================================
# TestSocialSerialization
# ===========================================================================


class TestSocialSerialization:
    """Test YAML serialization of the social list."""

    def setup_method(self):
        self.enricher = PersonEnricher(Path("/tmp/fake"))

    def test_block_style_list(self):
        fm = {
            "company": "Red Hat",
            "mail": "jdoe@redhat.com",
            "social": ["https://github.com/jdoe", "https://gitlab.cee.redhat.com/jdoe"],
            "tags": ["People"],
        }
        output = self.enricher.serialize_frontmatter(fm, "# Bio\n")
        assert "- https://github.com/jdoe" in output
        assert "- https://gitlab.cee.redhat.com/jdoe" in output
        # Should NOT be flow style
        assert "[https://github.com" not in output

    def test_long_urls_not_wrapped(self):
        long_url = "https://gitlab.cee.redhat.com/very/long/path/" + "x" * 80
        fm = {
            "mail": "jdoe@redhat.com",
            "social": [long_url],
        }
        output = self.enricher.serialize_frontmatter(fm, "")
        # The full URL must appear on one line
        for line in output.split("\n"):
            if long_url in line:
                assert line.strip() == f"- {long_url}"
                break
        else:
            pytest.fail(f"Long URL not found on a single line in output:\n{output}")

    def test_round_trip(self):
        fm = {
            "company": "Red Hat",
            "mail": "jdoe@redhat.com",
            "social": ["https://github.com/jdoe", "https://gitlab.cee.redhat.com/jdoe"],
            "tags": ["People"],
            "title": "Engineer",
        }
        serialized = self.enricher.serialize_frontmatter(fm, "# Body\n")
        parsed, body = self.enricher.parse_frontmatter(serialized)
        assert parsed == fm
        assert body == "# Body\n"


# ===========================================================================
# TestDryRun
# ===========================================================================


class TestDryRun:
    """Test dry-run mode does not modify files but still reports changes."""

    LDAP_DATA = {
        "title": "Engineer",
        "mail": "jdoe@redhat.com",
        "social": ["https://github.com/jdoe"],
    }

    def test_dry_run_no_file_modification(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\ntags:\n  - People\n"
        path = _make_person_file(tmp_path, "Jane Doe", fm_yaml)
        original_content = path.read_text()

        enricher = PersonEnricher(tmp_path, dry_run=True)
        with patch.object(enricher, "get_ldap_data", return_value=self.LDAP_DATA):
            result = enricher.enrich_person_file(path)

        assert result is True
        assert path.read_text() == original_content

    def test_dry_run_reports_changes(self, tmp_path):
        fm_yaml = "mail: jdoe@redhat.com\ntags:\n  - People\n"
        path = _make_person_file(tmp_path, "Jane Doe", fm_yaml)

        enricher = PersonEnricher(tmp_path, dry_run=True)
        with patch.object(enricher, "get_ldap_data", return_value=self.LDAP_DATA):
            enricher.enrich_person_file(path)

        assert len(enricher.updated_files) == 1
        changes = enricher.updated_files[0]["changes"]
        assert any("social" in c for c in changes)


# ===========================================================================
# TestEnrichAllWithSocial
# ===========================================================================


class TestEnrichAllWithSocial:
    """End-to-end test: enrich_all() with social URL support."""

    def test_enriches_file_with_social_and_skips_not_found(self, tmp_path):
        # File 1: has email, LDAP returns social URLs
        _make_person_file(tmp_path, "Alice Smith",
                          "mail: asmith@redhat.com\ntags:\n  - People\n")
        # File 2: has email, LDAP returns no data (not found)
        _make_person_file(tmp_path, "Bob Jones",
                          "mail: bjones@redhat.com\ntags:\n  - People\n")

        ldap_alice = {
            "title": "Manager",
            "mail": "asmith@redhat.com",
            "social": ["https://github.com/asmith"],
        }

        def fake_ldap(email):
            if email == "asmith@redhat.com":
                return ldap_alice
            return None

        enricher = PersonEnricher(tmp_path)
        with patch.object(enricher, "get_ldap_data", side_effect=fake_ldap):
            enricher.enrich_all()

        assert len(enricher.updated_files) == 1
        assert enricher.updated_files[0]["name"] == "Alice Smith"

        alice_fm = _read_frontmatter(tmp_path / "Alice Smith.md")
        assert alice_fm["social"] == ["https://github.com/asmith"]

        bob_fm = _read_frontmatter(tmp_path / "Bob Jones.md")
        assert "social" not in bob_fm

    def test_enriches_preserves_existing_social_in_batch(self, tmp_path):
        _make_person_file(tmp_path, "Carol Dae",
                          "mail: cdae@redhat.com\nsocial:\n  - https://personal.blog/carol\ntags:\n  - People\n")

        ldap_carol = {
            "title": "Dev",
            "mail": "cdae@redhat.com",
            "social": ["https://github.com/cdae"],
        }

        enricher = PersonEnricher(tmp_path)
        with patch.object(enricher, "get_ldap_data", return_value=ldap_carol):
            enricher.enrich_all()

        fm = _read_frontmatter(tmp_path / "Carol Dae.md")
        assert "https://personal.blog/carol" in fm["social"]
        assert "https://github.com/cdae" in fm["social"]
