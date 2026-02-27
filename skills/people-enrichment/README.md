# People Enrichment Skill

Enriches Person files in Obsidian vault with Red Hat employee data from LDAP.

## Overview

This skill processes Person files in the `PEOPLE/` directory and enriches them with Red Hat employee information from LDAP, including job titles, locations, email addresses, and mobile numbers.

## Features

- **Email Discovery**: Searches for employees via Google People Search when email is not in frontmatter
- **LDAP Enrichment**: Queries Red Hat LDAP for employee data
- **Field Updates**: Adds/updates `title`, `rhatLocation`, `mail`, `mobile`
- **Field Normalization**: Renames existing `email` field to `mail` for consistency
- **Preservation**: Maintains all original frontmatter fields and body content
- **Reporting**: Generates summary of updates and list of not-found people for manual review

## Usage

### Full Enrichment

```bash
uv run --with pyyaml enrich_people.py "/path/to/vault/PEOPLE"
```

### Dry Run (Preview Changes)

```bash
uv run --with pyyaml enrich_people.py "/path/to/vault/PEOPLE" --dry-run
```

### Test Mode (Process First N Files)

```bash
uv run --with pyyaml enrich_people.py "/path/to/vault/PEOPLE" --limit 10
```

## Requirements

- Python 3.7+
- `pyyaml` package (automatically installed by `uv`)
- `ldapsearch` command (available on macOS/Linux)
- `gog` command (Google CLI tool)
- Access to Red Hat LDAP server (ldaps://ldap.corp.redhat.com)

## Lookup Strategy

1. **If email/mail exists in frontmatter**: Query LDAP directly with `(mail=<email>)`
2. **If no email**: Search by filename with `gog people search "<name>"` to get email, then LDAP
3. **If not found**: Skip and add to not-found report (manual review needed)

## Fields Added/Updated

| Field | Source | Description | Example |
|-------|--------|-------------|---------|
| `title` | LDAP | Job title | "Director, Engineering" |
| `rhatLocation` | LDAP | Red Hat office location | "RH - Lowell" or "Remote US MA" |
| `mail` | LDAP | Email address | "areis@redhat.com" |
| `mobile` | LDAP | Mobile phone number | "+19782278014" |

## Original Fields Preserved

All original frontmatter fields are preserved:
- `company`
- `location`
- `aliases`
- `tags`
- Any other custom fields

## Example Output

### Before
```yaml
---
company: Red Hat
location: Boston
email: areis@redhat.com
aliases:
tags:
  - People
---
```

### After
```yaml
---
company: Red Hat
location: Boston
aliases:
tags:
  - People
title: Director, Engineering
rhatLocation: RH - Lowell
mail: areis@redhat.com
mobile: '+19782278014'
---
```

## Reports

The script generates:

1. **Updated Files**: List of successfully enriched people with changes made
2. **Not Found**: List of people who couldn't be found in LDAP (for manual review)
3. **Errors**: Any processing errors encountered

## Implementation Details

- Uses YAML for frontmatter parsing/serialization
- Handles empty fields gracefully (no "null" values in output)
- Preserves original YAML formatting style
- Timeout protection on external commands (10 seconds)
- Alphabetical processing order for consistent results

## Not Found Cases

People may not be found if:
- They've left Red Hat (no longer in LDAP)
- Their name differs in the directory (use different spelling)
- They're not Red Hat employees
- Network/authentication issues

For these cases, manually verify and update the person file if needed.
