---
name: people-research
description: Research a person and create/update an Obsidian People note with a comprehensive dossier (career history, technical focus, connection context) using LinkedIn, GitHub, web search, and LDAP
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - Edit
  - WebSearch
  - WebFetch
  - AskUserQuestion
  - mcp__linkedin__get_person_profile
  - mcp__linkedin__search_people
  - mcp__linkedin__get_company_profile
---

# People Research

Researches a person from LinkedIn, GitHub, the web, and Red Hat LDAP, then creates or updates an Obsidian People note with a structured dossier: career arc, technical focus, connection context, and links.

## When to Use

Invoke `/people-research [name or context]` when:
- Preparing for a meeting with someone you don't know well
- Creating a People note for a new contact
- Enriching an existing sparse People note with research
- A calendar attendee doesn't have a People note yet

Works for external contacts (dossier format) and Red Hat employees (vault template format with LDAP enrichment). Complements `/people-enrichment`, which does batch LDAP updates for Red Hat contacts — this skill does deep single-person research across multiple sources.

Input can be:
- A full name: `/people-research Spencer Smith`
- A name + company: `/people-research Spencer Smith at SideroLabs`
- A meeting reference: `/people-research the Spencer from my 11am meeting today`
- A LinkedIn username or email

## Workflow

### Step 1 — Discover Vault Root

```bash
cat ~/Library/Application\ Support/obsidian/obsidian.json
```

Parse the JSON to find the vault with `"open": true`. Extract `vault_root`.

### Step 2 — Resolve Person Identity

If the input is a **meeting reference** (e.g., "Spencer from my 2pm" or "the person on my calendar today"):
1. Search `<vault_root>/MEETINGS/` for the meeting file matching the description
2. Read the file's `attendees` frontmatter field to identify the target person

If the input is a **name** (with or without company hint):
1. Check if a People file already exists: `<vault_root>/PEOPLE/<First Last>.md`
2. If it exists, read frontmatter — extract any known email, LinkedIn username, or company. These seed the research.

### Step 3 — Search and Disambiguate

Run searches in parallel:
- `mcp__linkedin__search_people` with the person's name (and company if known)
- `WebSearch` with query: `"<Full Name>" <company if known> site:linkedin.com`

**If one strong match** (name + company align, or only one plausible result): State who was found — "Found Spencer Smith, Senior Director at Sidero Labs (Greenville, SC) — proceeding." — and continue without asking. The user can interrupt if wrong.

**If multiple plausible matches**: Present the top candidates using `AskUserQuestion`:

> I found multiple people matching "Spencer Smith". Which one?
> 1. Spencer Smith — Senior Director at Sidero Labs (Greenville, SC)
> 2. Spencer Smith — Software Engineer at Google (Mountain View, CA)
> 3. Spencer Smith — VP AI Engineering at Coupa (Phoenix)

Never guess when the person is ambiguous. Always confirm before proceeding.

### Step 4 — Gather Connection Context

Ask the user one question (unless they already provided this in their request):

> How do you know [Name]? (e.g., intro via Tim Gerla, met at KubeCon, former colleague at Red Hat)

This is required for the "Connection to James" dossier section and cannot be inferred from any data source. If the user mentioned context in the initial request ("my meeting with Spencer via Tim Gerla"), extract it and skip asking.

### Step 5 — Deep Research: LinkedIn

```
mcp__linkedin__get_person_profile(linkedin_username, sections="experience,education")
```

Extract:
- Current title and company
- Full work history (company, role, start/end dates)
- Education (school, degree, graduation year)
- Location (city, state/country)
- LinkedIn username (for `aliases` frontmatter field). Construct `https://www.linkedin.com/in/<linkedin_username>` for inclusion in the `social` frontmatter list.
- Profile URN (for `mcp__linkedin__get_company_profile` if needed)

**Optional:** If the company is unfamiliar or important to context, call `mcp__linkedin__get_company_profile` to get company description, size, and specialties — use for the "Role & Organization" section.

### Step 6 — Deep Research: Web + GitHub

Run searches:
```
WebSearch: "<Full Name>" <company> github
WebSearch: "<Full Name>" blog OR podcast OR speaker OR "open source"
```

If a GitHub username is found, fetch the profile:
```
WebFetch: https://github.com/<username>
```

Extract:
- Bio and location
- Public repo count
- Pinned/starred repositories (name, stars, description) — especially any with 1k+ stars
- Organization memberships

Note any blog, podcast, YouTube channel, or conference talk credits for the Links section. If a GitHub username is confirmed, construct `https://github.com/<username>` for inclusion in the `social` frontmatter list.

### Step 7 — Deep Research: LDAP (Red Hat contacts only)

Classify as Red Hat if: email is `@redhat.com`, the company is Red Hat, or the user indicated they are a Red Hat employee.

If Red Hat and email is known:
```bash
ldapsearch -x -H ldaps://ldap.corp.redhat.com -b dc=redhat,dc=com \
  "(mail=<email>)" title rhatLocation mail mobile
```

If Red Hat but email is unknown:
```bash
gog people search "<Full Name>"
```
Parse email from the output (last column of the tabular result), then run the LDAP query above.

Parse LDIF output line-by-line for: `title:`, `rhatLocation:`, `mail:`, `mobile:` attributes.

### Step 8 — Generate the People Note

**Classify the person:**
- Red Hat: `@redhat.com` email, LDAP returned data, or user confirmed → `tags: [People]`, use vault template format
- External (everyone else) → `tags: [type/person, external]`, use dossier format

**Check for existing file:** `<vault_root>/PEOPLE/<First Last>.md`
- **No file** → create it (see formats below)
- **File exists with `%% section:dossier %%` markers** → replace dossier content, update frontmatter fields
- **File exists without dossier markers** → update empty sections only; if sections have existing hand-written content, report what's new and let the user decide

When updating an existing file, merge newly discovered social URLs into the existing `social` list without removing manually-added entries. Append only URLs not already present.

**Name conflict check:** If the file exists but frontmatter shows a different company or email than the researched person, stop and alert the user. Suggest `<First Last> (<Company>).md` as an alternative filename.

---

#### New File — External Contact (Dossier Format)

Follow the pattern established in Tim Gerla.md, Dustin Kirkland.md, and Justin Garrison.md:

```markdown
---
company: <company name>
location: <city, state/country>
email: <email if known, else leave blank>
pronouns: <if publicly stated, else omit field>
title: <current job title>
aliases:
  - <linkedin_username>
  - <github_username if different from linkedin_username>
social:
  - https://github.com/<username>
  - https://www.linkedin.com/in/<username>
tags:
  - type/person
  - external
---

%% section:dossier %%
> [!info] Auto-generated on YYYY-MM-DD

## Dossier

### Role & Organization

<1-2 paragraphs: current title, what the company does (products, customers, funding if notable), team context. Aim for depth — not just the company tagline.>

### Connection to James

<1 paragraph: how they know each other. Shared employers, mutual connections, who made the intro, what the meeting is for. Be specific — "Intro via Tim Gerla, informational networking conversation" is better than "Professional contact.">

### Career Arc

| Period | Role | Company |
|---|---|---|
| YYYY – YYYY | Role title | Company name |

<Narrative paragraphs for each significant stint — 2-4 sentences per company. Focus on what they built, shipped, or led. Skip narrative for short (<1 yr) or unremarkable roles. Highlight acquisitions, notable open source projects, awards, or inflection points.>

### Technical Focus

- <Core technical domain or area of expertise>
- <Notable open source project, tool, or standard they contributed to>
- <Language/platform/approach they're known for>

### Personal / Community

<Bullets covering: hobbies, community involvement, education (school + degree), content creation (blog, podcast, YouTube, book), conference speaking, awards. Include only publicly available information. Omit if nothing notable is found.>

%% /section:dossier %%

## Links

| | |
|---|---|
| Company | <company URL> |
| LinkedIn | https://www.linkedin.com/in/<username>/ |
| GitHub | https://github.com/<username> |
| Twitter/X | <if found> |
| Blog | <if found> |
| Email | <if known> |

## Notable Projects

<Include only if GitHub repos with 500+ stars were found. Omit section entirely otherwise.>

| Repo | Stars | About |
|---|---|---|
| [name](https://github.com/user/repo) | Nk | One-line description |

## Parking Lot

<Seed with context from the meeting/introduction. Examples: "Intro via [[Tim Gerla]] — informational conversation", "Ask who else I should talk to at the end of every conversation.">

## Meetings

\```base
views:
  - type: table
    name: Table
    filters:
      and:
        - file.tags.contains("Meetings")
        - attendees.contains(link("[[<Full Name>]]"))
    order:
      - file.name
      - attendees
    sort:
      - property: file.ctime
        direction: DESC
    limit: 10
\```
```

---

#### New File — Red Hat Contact (Vault Template Format)

Use the standard People template structure, pre-populated with research:

```markdown
---
company: Red Hat
location: <city if known from LinkedIn or LDAP>
email: <from LDAP mail field>
aliases:
  - <kerberos ID or LinkedIn username if found>
tags:
  - People
title: <from LDAP>
rhatLocation: <from LDAP>
mail: <from LDAP>
mobile: <from LDAP if present>
social:
  - https://github.com/<username>
  - https://www.linkedin.com/in/<username>
---

# Family Info

# Work History

<If LinkedIn data was gathered: Career history using H2 headings per company, same narrative style as external contacts. If no LinkedIn data: leave section empty.>

# Fun facts

<GitHub profile, blog, conference talks, notable open source contributions, community involvement — if found. Otherwise leave empty.>

# Quarterly Highlights

# Parking Lot

<Seed with connection/meeting context if relevant.>

## Recent Meetings

\```base
views:
  - type: table
    name: Table
    filters:
      and:
        - file.tags.contains("Meetings")
        - attendees.contains(link("[[<Full Name>]]"))
    order:
      - file.name
      - attendees
    sort:
      - property: file.ctime
        direction: DESC
    limit: 10
\```
```

---

#### Updating an Existing File with Dossier Markers

Use the Edit tool to replace content between the dossier markers:

1. Read the current file content
2. Find `%% section:dossier %%` and `%% /section:dossier %%`
3. Replace everything between (and including) those lines with freshly generated dossier content
4. Update frontmatter fields that have changed (title, company, location)
5. Preserve everything outside the dossier block (Links, Notable Projects, Parking Lot, Meetings query — unchanged)

#### Updating an Existing File Without Dossier Markers

1. Update frontmatter fields: title, company, location — if changed per LinkedIn/LDAP
2. For each body section (Work History, Fun facts): if the section is empty (just the heading, no content), populate it
3. If a section has existing content: report to the user what new information was found — "Your Work History has 2 entries; LinkedIn shows 5. New roles found: [list them]. Add them?" — then act on the response
4. Add a dossier block + `## Links` section at the end if not present, before `## Recent Meetings`

### Step 9 — Report to User

Report:
- File path created or updated (relative to vault root)
- Data sources used: LinkedIn ✓, GitHub ✓, LDAP ✗ (not Red Hat), etc.
- Key facts: current title, company, location
- Any gaps: "GitHub profile not found", "LDAP unreachable — skipped Red Hat fields"
- First 3-4 lines of the generated dossier as a preview

## Edge Cases

**Sparse data:** Omit dossier sections that have nothing to fill. A note with only Role & Organization and Career Arc is still useful. Never write "No information available" — just skip the section.

**Person recently changed companies:** LinkedIn is the source of truth. If frontmatter shows a different company from what LinkedIn reports, update frontmatter and note the change in the report.

**Name conflicts in PEOPLE/:** If `First Last.md` exists but frontmatter shows a different person (different company + email), stop. Alert the user and suggest `First Last (Company).md`.

**Meeting query format:** New files always use `base` syntax. If updating an existing file that uses `dataview` syntax, preserve it.

**LinkedIn unavailable:** Fall back to WebSearch + WebFetch on `linkedin.com/in/<username>` for partial data. Note the degraded coverage in the report.

**No GitHub found:** Omit the Notable Projects section entirely. Do not guess at GitHub usernames.

## Output Format Reference

The three existing dossier-format People notes are canonical examples. Read one before generating:
- `PEOPLE/Tim Gerla.md` — connection via shared Ansible / Red Hat history, currently between roles
- `PEOPLE/Dustin Kirkland.md` — rich career arc table, Technical Focus section with open source projects
- `PEOPLE/Justin Garrison.md` — Notable Projects table, Community/Interests section with hobbies

## Related Skills

- **people-ldap-sync**: Batch LDAP sync for Red Hat contacts — use for bulk frontmatter updates across all People files
- **obsidian-vault-discovery**: Vault root discovery pattern used in Step 1
- **daily-planner**: Creates meeting files that link to People notes via attendees frontmatter
