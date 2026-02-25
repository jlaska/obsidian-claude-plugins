---
company:
location:
email:
aliases:
  -
tags:
  - People
created: <% tp.file.creation_date() %>
<%*
// Auto-file this note to PEOPLE/ directory
const targetFolder = "PEOPLE";
const currentFolder = tp.file.folder(true);
if (currentFolder !== targetFolder) {
  await tp.file.move("/" + targetFolder + "/" + tp.file.title);
}
%>
---

# Contact


---

# Family Info


---

# Work History


---

# Interests


---

# Recent Meetings

```dataview
TABLE WITHOUT ID
  file.link as "Meeting",
  dateformat(date(created), "yyyy-MM-dd") as "Date"
FROM "MEETINGS"
WHERE contains(attendees, this.file.name)
SORT created DESC
LIMIT 10
```
