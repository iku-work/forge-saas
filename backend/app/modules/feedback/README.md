# feedback

A signed-in user sends a message from the app ("Send feedback" in the sidebar
footer); superadmins read it in the admin **Feedback** inbox and mark it
handled. Each message records who sent it, the page it was sent from, and the
org they were working in.

Registered with the `admin` module: without an admin area nobody could read it,
so the form and the route go away together. The table is core and always exists.

## Public interface

- `POST /api/v1/feedback` — `{category: "bug" | "idea" | "other", message, page?, org_id?}`.
  `org_id` must be one of the sender's orgs (403 otherwise); `page` must be a path,
  with no query string. Rate limited like other sensitive writes (3/min per user).
- `GET /api/v1/admin/feedback?status=open|resolved` — the inbox, newest first, by cursor.
- `POST /api/v1/admin/feedback/{id}/resolve` and `/reopen`.

`FeedbackService` is what other modules call (the admin module does).

## Events emitted

- Notification `feedback_received` to every superadmin except the sender: in the
  app and by email by default. Sent with no org, so it reaches a superadmin who
  is not a member of the sender's org.

## Personal data

Deleted by the GDPR purge and included in the data export. See
`docs/security/data-classification.md`.
