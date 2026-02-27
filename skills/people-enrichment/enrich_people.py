#!/usr/bin/env python3
"""
Enrich People files in Obsidian vault with Red Hat employee data from LDAP.

This script:
1. Reads all Person files from the vault's PEOPLE/ directory
2. Looks up each person via gog people search (if no email) and LDAP
3. Adds/updates frontmatter fields: title, rhatLocation, mail, mobile
4. Renames existing 'email' field to 'mail' for consistency
5. Generates reports of updated files and not-found people
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import yaml


class PersonEnricher:
    """Enriches Person files with Red Hat employee data."""

    def __init__(self, people_dir: Path, dry_run: bool = False):
        self.people_dir = people_dir
        self.dry_run = dry_run
        self.updated_files = []
        self.not_found = []
        self.errors = []

    def parse_frontmatter(self, content: str) -> Tuple[Optional[Dict], str]:
        """Parse YAML frontmatter from markdown file.

        Returns:
            Tuple of (frontmatter_dict, body_content)
        """
        # Match YAML frontmatter between --- delimiters
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
        if not match:
            return None, content

        try:
            frontmatter = yaml.safe_load(match.group(1))
            body = match.group(2)
            return frontmatter, body
        except yaml.YAMLError as e:
            return None, content

    def serialize_frontmatter(self, frontmatter: Dict, body: str) -> str:
        """Serialize frontmatter dict and body back to markdown."""
        # Custom representer to output None as empty string instead of "null"
        def represent_none(self, data):
            return self.represent_scalar('tag:yaml.org,2002:null', '')

        yaml.add_representer(type(None), represent_none)

        yaml_str = yaml.dump(
            frontmatter,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            default_style=None,
            width=float("inf")  # Prevent line wrapping
        )
        return f"---\n{yaml_str}---\n{body}"

    def get_email_from_gog(self, name: str) -> Optional[str]:
        """Search for person via gog and extract email."""
        try:
            result = subprocess.run(
                ['gog', 'people', 'search', name],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return None

            # Parse output - format is:
            # RESOURCE                      NAME         EMAIL
            # people/107802665765765929344  Ademar Reis  areis@redhat.com
            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return None

            # Find email in the second line (first data row)
            data_line = lines[1]
            # Email is typically the last column
            parts = data_line.split()
            if parts and '@' in parts[-1]:
                return parts[-1]

            return None

        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            print(f"  ⚠️  gog search failed for {name}: {e}", file=sys.stderr)
            return None

    def get_ldap_data(self, email: str) -> Optional[Dict[str, str]]:
        """Query LDAP for person's data by email.

        Returns:
            Dict with keys: title, rhatLocation, mail, mobile (if available)
        """
        try:
            result = subprocess.run(
                [
                    'ldapsearch',
                    '-x',
                    '-H', 'ldaps://ldap.corp.redhat.com',
                    '-b', 'dc=redhat,dc=com',
                    f'(mail={email})',
                    'title', 'rhatLocation', 'mail', 'mobile'
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return None

            # Parse LDIF output
            data = {}
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('title: '):
                    data['title'] = line[7:]
                elif line.startswith('rhatLocation: '):
                    data['rhatLocation'] = line[14:]
                elif line.startswith('mail: '):
                    data['mail'] = line[6:]
                elif line.startswith('mobile: '):
                    data['mobile'] = line[8:]

            # Only return if we found at least mail
            if 'mail' in data:
                return data
            return None

        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            print(f"  ⚠️  LDAP query failed for {email}: {e}", file=sys.stderr)
            return None

    def enrich_person_file(self, file_path: Path) -> bool:
        """Enrich a single person file.

        Returns:
            True if file was updated, False otherwise
        """
        try:
            content = file_path.read_text(encoding='utf-8')
            frontmatter, body = self.parse_frontmatter(content)

            if frontmatter is None:
                self.errors.append(f"{file_path.name}: No frontmatter found")
                return False

            # Extract person name from filename
            name = file_path.stem

            # Get email - either from frontmatter or via gog
            email = frontmatter.get('email') or frontmatter.get('mail')

            if not email:
                print(f"  🔍 Searching gog for: {name}")
                email = self.get_email_from_gog(name)
                if not email:
                    self.not_found.append(name)
                    print(f"  ❌ Not found: {name}")
                    return False

            # Query LDAP for data
            print(f"  🔍 Querying LDAP for: {name} ({email})")
            ldap_data = self.get_ldap_data(email)

            if not ldap_data:
                self.not_found.append(f"{name} ({email})")
                print(f"  ❌ LDAP data not found: {name}")
                return False

            # Track what changes we're making
            changes = []

            # Rename 'email' to 'mail' if present
            if 'email' in frontmatter:
                del frontmatter['email']
                changes.append("email→mail")

            # Add/update fields from LDAP
            for field in ['title', 'rhatLocation', 'mail', 'mobile']:
                if field in ldap_data:
                    old_value = frontmatter.get(field)
                    new_value = ldap_data[field]
                    if old_value != new_value:
                        frontmatter[field] = new_value
                        if old_value:
                            changes.append(f"{field} updated")
                        else:
                            changes.append(f"{field} added")

            if not changes:
                print(f"  ✓ No changes needed: {name}")
                return False

            # Write updated content
            if not self.dry_run:
                new_content = self.serialize_frontmatter(frontmatter, body)
                file_path.write_text(new_content, encoding='utf-8')

            self.updated_files.append({
                'name': name,
                'file': file_path.name,
                'changes': changes,
                'data': ldap_data
            })
            print(f"  ✅ Updated: {name} ({', '.join(changes)})")
            return True

        except Exception as e:
            self.errors.append(f"{file_path.name}: {str(e)}")
            print(f"  ⚠️  Error processing {file_path.name}: {e}", file=sys.stderr)
            return False

    def enrich_all(self):
        """Enrich all person files in the directory."""
        md_files = sorted(self.people_dir.glob('*.md'))
        total = len(md_files)

        print(f"\n{'='*60}")
        print(f"Enriching {total} People files")
        print(f"Directory: {self.people_dir}")
        print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        print(f"{'='*60}\n")

        for i, file_path in enumerate(md_files, 1):
            print(f"[{i}/{total}] {file_path.name}")
            self.enrich_person_file(file_path)

        self.print_summary()

    def print_summary(self):
        """Print summary of enrichment results."""
        print(f"\n{'='*60}")
        print("ENRICHMENT SUMMARY")
        print(f"{'='*60}\n")

        print(f"✅ Updated: {len(self.updated_files)}")
        print(f"❌ Not found: {len(self.not_found)}")
        print(f"⚠️  Errors: {len(self.errors)}\n")

        if self.updated_files:
            print("Updated Files:")
            print("-" * 60)
            for item in self.updated_files:
                print(f"  • {item['name']}")
                print(f"    Changes: {', '.join(item['changes'])}")
                if 'title' in item['data']:
                    print(f"    Title: {item['data']['title']}")
                if 'rhatLocation' in item['data']:
                    print(f"    Location: {item['data']['rhatLocation']}")
                print()

        if self.not_found:
            print("Not Found (manual review needed):")
            print("-" * 60)
            for name in self.not_found:
                print(f"  • {name}")
            print()

        if self.errors:
            print("Errors:")
            print("-" * 60)
            for error in self.errors:
                print(f"  • {error}")
            print()


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Enrich People files with Red Hat LDAP data')
    parser.add_argument('people_dir', type=Path, help='Path to PEOPLE directory')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be changed without modifying files')
    parser.add_argument('--limit', type=int, help='Limit processing to first N files (for testing)')

    args = parser.parse_args()

    if not args.people_dir.exists():
        print(f"Error: Directory not found: {args.people_dir}", file=sys.stderr)
        sys.exit(1)

    enricher = PersonEnricher(args.people_dir, dry_run=args.dry_run)

    # For testing with limit
    if args.limit:
        print(f"\n⚠️  Testing mode: Processing only first {args.limit} files\n")
        md_files = sorted(args.people_dir.glob('*.md'))[:args.limit]
        for i, file_path in enumerate(md_files, 1):
            print(f"[{i}/{len(md_files)}] {file_path.name}")
            enricher.enrich_person_file(file_path)
        enricher.print_summary()
    else:
        enricher.enrich_all()


if __name__ == '__main__':
    main()
