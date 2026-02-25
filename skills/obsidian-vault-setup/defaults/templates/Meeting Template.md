---
attendees:
  -
tags:
  - Meetings
created: <% tp.file.creation_date() %>
---

# Actions


---

# Agenda


---

# Recent Meetings

```dataview
TABLE WITHOUT ID
  file.link as "Meeting",
  attendees as "Attendees",
  dateformat(date(created), "yyyy-MM-dd") as "Date"
FROM "MEETINGS"
WHERE contains(attendees, this.attendees[0])
  AND file.name != this.file.name
SORT created DESC
LIMIT 5
```
