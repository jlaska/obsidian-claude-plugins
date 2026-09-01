---
name: people-ldap-sync
description: Batch-sync Red Hat LDAP directory data (job titles, locations, email, mobile, social profile URLs) into Obsidian People note frontmatter fields
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Bash
  - Write
---

# People LDAP Sync

Batch-syncs Red Hat LDAP directory data into Obsidian People note frontmatter fields, including job titles, office locations, email addresses, mobile numbers, and social profile URLs.

## When to Use

Invoke `/people-ldap-sync` when:
- You want to bulk-update People files with current Red Hat employee data
- You've created new Person files and need to populate employee information
- You need to refresh/update employee information for existing contacts
- You want to normalize email fields from `email:` to `mail:` for consistency

## Features

- **Email Discovery**: Searches for employees via Google People Search when email is not in frontmatter
- **LDAP Enrichment**: Queries Red Hat LDAP for employee data
- **Field Updates**: Adds/updates `title`, `rhatLocation`, `mail`, `mobile`
- **Social URL Discovery**: Extracts social profile URLs (GitHub, GitLab, etc.) from LDAP `rhatSocialURL` attribute
- **Field Normalization**: Renames existing `email` field to `mail` for consistency
- **Preservation**: Maintains all original frontmatter fields and body content
- **Reporting**: Generates summary of updates and list of not-found people for manual review

## Workflow

### 1. Discover Vault Root

Use the same logic as `obsidian-vault-discovery` skill:

```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Parse the JSON to find the vault with `"open": true`, or use the most recently opened vault.

### 2. Locate PEOPLE Directory

Find the PEOPLE directory in the vault:

```bash
ls -d "<vault_root>/PEOPLE"
```

If the directory doesn't exist, prompt the user or exit gracefully.

### 3. Run Enrichment Script

Execute the enrichment script with options:

**Full Enrichment:**
```bash
uv run --with pyyaml skills/people-ldap-sync/enrich_people.py "<vault_root>/PEOPLE"
```

**Dry Run (Preview Changes):**
```bash
uv run --with pyyaml skills/people-ldap-sync/enrich_people.py "<vault_root>/PEOPLE" --dry-run
```

**Test Mode (First N Files):**
```bash
uv run --with pyyaml skills/people-ldap-sync/enrich_people.py "<vault_root>/PEOPLE" --limit 10
```

### 4. Review Results

The script processes all Person files and reports:
- **Updated Files**: Successfully enriched people with changes made
- **Not Found**: People who couldn't be found in LDAP (for manual review)
- **Errors**: Any processing errors encountered

## Lookup Strategy

The enrichment follows this cascade:

1. **If email/mail exists in frontmatter**: Query LDAP directly with `(mail=<email>)`
2. **If no email**: Search by filename with `gog people search "<name>"` to get email, then LDAP
3. **If not found**: Skip and add to not-found report (manual review needed)

## Fields Added/Updated

| Field | Source | Description | Example |
|-------|--------|-------------|---------|
| `title` | LDAP | Job title | "Senior Software Engineer" |
| `rhatLocation` | LDAP | Red Hat office location | "RH - Raleigh" or "Remote US NC" |
| `mail` | LDAP | Email address | "jdoe@redhat.com" |
| `mobile` | LDAP | Mobile phone number | "+19195551234" |
| `social` | LDAP (`rhatSocialURL`) | Social profile URLs (GitHub, GitLab, etc.) | `["https://github.com/jdoe"]` |

## Original Fields Preserved

All original frontmatter fields are preserved:
- `company`
- `location`
- `aliases`
- `tags`
- `social` (manually-added URLs are preserved; LDAP URLs are merged in)
- Any other custom fields

## Example Output

### Before
```yaml
---
company: Red Hat
location: Raleigh
email: jdoe@redhat.com
aliases:
tags:
  - People
---
```

### After
```yaml
---
company: Red Hat
location: Raleigh
aliases:
tags:
  - People
title: Senior Software Engineer
rhatLocation: RH - Raleigh
mail: jdoe@redhat.com
mobile: '+19195551234'
social:
  - https://github.com/jdoe
---
```

## Requirements

- Python 3.7+
- `pyyaml` package (automatically installed by `uv`)
- `ldapsearch` command (available on macOS/Linux)
- `gog` command (Google CLI tool)
- Access to Red Hat LDAP server (ldaps://ldap.corp.redhat.com)

## Not Found Cases

People may not be found if:
- They've left Red Hat (no longer in LDAP)
- Their name differs in the directory (use different spelling)
- They're not Red Hat employees
- Network/authentication issues

For these cases, manually verify and update the person file if needed. The script prints not-found people to stdout for review.

## Implementation Details

- Uses YAML for frontmatter parsing/serialization
- Handles empty fields gracefully (no "null" values in output)
- Preserves original YAML formatting style
- Timeout protection on external commands (10 seconds)
- Alphabetical processing order for consistent results

## Related Skills

- **people-research**: Single-person interactive research using LinkedIn, GitHub, and web search — use for external contacts or deep dossier generation
- **obsidian-vault-discovery**: Used to discover vault configuration
- **daily-planner**: Uses People files for attendee matching in meetings
- **obsidian-vault-setup**: Creates PEOPLE/ directory structure
