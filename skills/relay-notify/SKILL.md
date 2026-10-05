---
name: relay-notify
description: Send notifications to a person through Relay (ali's notification gateway, delivered via Telegram), ask them a question with buttons or a typed reply, and read their answer back. Use this skill whenever an app or agent needs to tell a human something or get a decision from one — job finished or failed, alerts, reports, "let me know when…", "ping me", "tell ali", "send me a telegram", "ask before deploying", approvals, or waiting for human input — even if Relay isn't named. If you don't know who to notify, send to the default user (RELAY_USER, default "admin").
---

# Relay: notify a person, ask them, read the answer

Relay is a small HTTP service. You send it a structured message (who, how
urgent, content blocks); it formats the message for Telegram, delivers it,
retries on failure and keeps a log. You never write Telegram markup: send plain
values and Relay renders them with its one built-in layout.

## Setup

Environment variables, set by whoever runs your app:

| Variable | Required | What it is |
|---|---|---|
| `RELAY_URL` | yes | Relay's base URL (host), e.g. `https://relay.alialiabadi.ir` |
| `RELAY_API_KEY` | yes | This app's API key (`rk_...`). The admin creates one per app with `relay clients create <app-name>` |
| `RELAY_APP` | yes | Your app's name (e.g. `backup-script`), sent as every message's `source`. Relay records it, so the admin can see which app sends what and debug it; the reader sees "via backup-script" |
| `RELAY_USER` | no, default `admin` | Who this app sends to when no recipient is given: a recipient's username or alias. The old name `RELAY_ADMIN` still works when `RELAY_USER` is unset |

If `RELAY_URL`, `RELAY_API_KEY` or `RELAY_APP` is missing, stop and ask the user for it;
never guess or invent one. **The API key is a secret:** read it from the
environment only. Never print it, log it, echo it in a reply, put it in a
message, or commit it (not in code, `.env` files under version control, test
fixtures or examples).

## The tools

Two ready-made clients do the plumbing; use the one matching your app's
language, or call the HTTP API below directly from any other.

- **Go:** [`references/go.md`](references/go.md) has a single-file client to
  copy into your app (`relay.FromEnv()`, `Notify`, `Send`, `FileBlock`, `Ask`,
  `Answers`, `WaitForAnswer`), with usage. Read it when the app is in Go.
- **Python or shell:** `scripts/relay.py`, below.

### `scripts/relay.py`

[`scripts/relay.py`](scripts/relay.py) (Python 3.8+, standard library only) is
the quickest way to use Relay. It reads the variables above, sends to
`RELAY_USER` when you give no `--to`, adds an idempotency key so its own
retries never send twice, and prints JSON.

```bash
R=path/to/relay-notify/scripts/relay.py

# Notify
python3 $R notify "Nightly backup finished in 4m12s." --urgency low
python3 $R notify "Nightly backup of nas stopped." --title "Backup failed" --urgency high \
  --field Host=nas --field "Error=disk full"
# {"id": "msg_01J...", "status": "queued"}

# Attach a file (one per message, up to 5 MB; type guessed from the extension)
python3 $R notify "September invoice." --file invoice.pdf --file-caption "Due Oct 15"

# Ask with buttons and wait up to an hour for the answer
python3 $R ask "Ship v2.3 to production?" --option Yes --option No \
  --text "v2.3 passed staging. 14 commits, 2 migrations." --wait 3600
# {"id": "msg_01J...", "recipient": "ali", "answer": "Yes", "answered_at": "..."}

# Ask for a typed reply now, read the answer later
python3 $R ask "What should the new hostname be?"      # {"id": "msg_01J..."}
python3 $R answer msg_01J... --wait 600

# Anything else: a full POST /v1/messages body on stdin (tables, code, images, files, links)
python3 $R send < message.json

python3 $R status msg_01J...      # delivery status
python3 $R recipients             # who exists
```

Common options: `--to NAME` (repeatable), `--urgency low|normal|high|critical`,
`--title`, `--key` (your own idempotency key). Exit codes: `0` ok,
`1` Relay or network error (message on stderr, e.g.
`relay: 422 invalid_request: to[0]: unknown recipient`), `2` bad usage or
missing config, `3` nobody answered before `--wait` ran out (stdout has
`"answer": null`).

From Python, import it instead:

```python
import sys; sys.path.insert(0, "path/to/relay-notify/scripts")
import relay

relay.notify("Backup finished", urgency="low")
relay.notify("Logs attached", file="build.log", file_caption="Last run")
relay.send({"blocks": [relay.file_block("report.csv", caption="Weekly")]})
mid = relay.ask("Ship v2.3?", options=["Yes", "No"])
got = relay.wait_for_answer(mid, timeout_s=3600)   # None if nobody answered
if got and got["answer"] == "Yes":
    ...
```

Errors raise `relay.RelayError` with `.status`, `.code` and `.problems`.
Other languages: call the HTTP API below directly, and always send
`source` set to `RELAY_APP`.

## Who to send to

`to` takes 1-10 names. A name is a recipient's username or one of their
aliases. **If you don't know who to notify, send to `RELAY_USER`** (`admin`
unless set): the person this app reports to. Two names for the same person
are delivered once, so `["admin", "ali"]` is safe.

```bash
curl -sS "$RELAY_URL/v1/recipients" -H "Authorization: Bearer $RELAY_API_KEY"
# {"recipients":[{"username":"ali","aliases":["admin"],"display_name":"Ali",...,"linked_channels":["telegram"]}]}
```

A recipient with an empty `linked_channels` can't receive anything yet. If the
admin name comes back as `unknown recipient`, the alias doesn't exist on this
Relay: ask the user who to notify (the admin adds one with
`relay recipients alias <username> admin`). Only read recipients: creating,
changing and removing them is the admin's job, even though the API allows it.

## The HTTP API

Every request sends `Authorization: Bearer $RELAY_API_KEY`. Only `/healthz` is
public.

| Method | Path | Does |
|---|---|---|
| `POST` | `/v1/messages` | Send a message (and ask a question) |
| `GET` | `/v1/messages/{id}/answers` | Answers to its question so far; reading makes them final |
| `GET` | `/v1/messages/{id}` | Delivery status, never content |
| `GET` | `/v1/messages` | Your app's messages, newest first (`status`, `since`, `limit`, `cursor`) |
| `POST` | `/v1/preview` | Render a message without sending it |
| `GET` | `/v1/recipients` | Who can be notified |
| `GET` | `/v1/channels` | Channels this Relay can send on (Telegram for now) |

### Send a message

```bash
curl -sS -X POST "$RELAY_URL/v1/messages" \
  -H "Authorization: Bearer $RELAY_API_KEY" -H "Content-Type: application/json" \
  -d '{"to": ["admin"], "source": "backup-script", "text": "Nightly backup finished in 4m12s."}'
# 202 {"id":"msg_01J...","status":"queued"}
```

`202` means accepted; delivery happens in the background, so don't wait for it
unless you need to. Richer messages use a `title` and `blocks` instead of `text`
(use one or the other):

```bash
curl -sS -X POST "$RELAY_URL/v1/messages" \
  -H "Authorization: Bearer $RELAY_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "to": ["admin"],
    "urgency": "high",
    "source": "backup-script",
    "title": "Backup failed",
    "idempotency_key": "backup-nas-2026-10-03",
    "blocks": [
      {"type": "text",   "text": "Nightly backup of nas stopped."},
      {"type": "fields", "items": [{"label": "Host", "value": "nas"}, {"label": "Error", "value": "disk full"}]},
      {"type": "table",  "columns": ["Disk", "Used"], "rows": [["sda", "98%"], ["sdb", "41%"]]},
      {"type": "code",   "text": "rsync: write failed: No space left on device"},
      {"type": "link",   "text": "Open dashboard", "url": "https://grafana.example/d/disks"}
    ]
  }'
```

Always set `source` to `RELAY_APP`: it's how the admin tells apps apart in
Relay's records, and the reader sees who's talking.

| Field | Notes |
|---|---|
| `to` | 1-10 usernames or aliases, no duplicates |
| `urgency` | `low`, `normal` (default), `high`, `critical`; see below |
| `title` | Optional, up to 256 chars, shown bold |
| `source` | Your app's name, up to 64 chars |
| `text` | Shorthand for one text block; don't combine with `blocks` |
| `blocks` | Up to 20 content blocks; see below |
| `idempotency_key` | Up to 128 chars; see Errors and retries |
| `channels` | Optional channel override; leave it out (Telegram is the only channel) |

### Urgency

Pick the lowest level that fits; people stop reading alerts that cry wolf.

- `low`: FYI, arrives silently (no sound). Routine reports, "done" messages nobody waits for.
- `normal`: someone should see it today.
- `high`: needs attention soon (a failed job, something degraded).
- `critical`: act now (outage, data at risk). Shown with 🚨. Use rarely.

### Blocks

There are no templates to pick or register: a message is built from these
blocks and Telegram's one layout renders them in order.

| `type` | Fields | Limits | Shown in Telegram as |
|---|---|---|---|
| `text` | `text` | 4000 chars | A paragraph |
| `fields` | `items: [{label, value}]` | 1-25 items, label 64, value 1024 | **Label:** value lines |
| `table` | `columns`, `rows` | 8 columns, 50 rows, cell 256; every row has one cell per column | Monospaced table; keep it narrow, it's read on a phone |
| `code` | `text` | 4000 chars | Monospaced block |
| `link` | `text`, `url` | text 64, `https` only | A button under the message |
| `image` | `url` (`https`) **or** `base64` + `content_type`, optional `caption` | 1 per message; PNG/JPEG up to 5 MB; caption 1024 | A photo. Use `base64` for images on private hosts: Telegram fetches `url` itself |
| `file` | `filename`, `base64`, optional `content_type`, optional `caption` | 1 per message; any type, 1 byte to 5 MB; filename 128, no `/`, `\` or control characters; caption 1024 | A document the reader can download. `content_type` defaults to `application/octet-stream` |
| `question` | `text`, optional `options`, optional `webhook` | see Ask a question | The question in bold at the end, with buttons |

Every value is plain text: `<b>`, Markdown and the like are shown literally,
not rendered. A message longer than Telegram allows is shortened with `…`, but
the title and the question always survive.

### Message recipes

Reusable shapes for common cases (with the tool, or as the JSON body):

- **Job done:** `notify "Import finished: 1,204 rows in 38s." --urgency low`
- **Job failed:** `--title "<job> failed" --urgency high`, a `text` block with what
  broke, `fields` for host / step / exit code, a `code` block with the last error
  lines, a `link` to the logs.
- **Report:** a `title`, one `text` summary line, a `table` of at most a few
  narrow columns, urgency `low`.
- **Approval:** a `title`, `text` with what will happen, a `question` with
  `options` like `["Approve", "Reject"]`; act only on the answer.
- **Free-text input:** a `question` with no `options`; validate what comes back.
- **Screenshot or chart:** one `image` block (`base64` for local files) with a short `caption`.
- **File (log, CSV, PDF):** a `text` line saying what it is, then one `file` block.
  Use `image` instead for pictures people should see inline. Over 5 MB: don't
  send it; say where to find it. An image and a file together must still fit
  the 7 MB request limit (base64 adds a third).

## Ask a question

Add one `question` block. With `options` the person gets one button per option;
without, they answer by replying to the message.

```bash
curl -sS -X POST "$RELAY_URL/v1/messages" \
  -H "Authorization: Bearer $RELAY_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "to": ["admin"],
    "source": "deploy-bot",
    "title": "Deploy v2.3?",
    "blocks": [
      {"type": "text", "text": "v2.3 passed staging. 14 commits, 2 migrations."},
      {"type": "question", "text": "Ship v2.3 to production?", "options": ["Yes", "No"]}
    ]
  }'
```

- `options`: 1-10 unique options, up to 64 chars each. Leave it out for a free-text reply.
- At most one question per message.
- `webhook` (optional): a public `https` URL. When an answer arrives, Relay POSTs
  `{"event": "answer", "message_id": "msg_..."}` to it: only the ID, never the
  answer. Fetch the answer from the API. Relay refuses private, loopback and
  internal addresses and doesn't follow redirects. The call is best effort
  (two retries), so poll as well if the answer matters.

### Read the answer

```bash
curl -sS "$RELAY_URL/v1/messages/$MESSAGE_ID/answers" -H "Authorization: Bearer $RELAY_API_KEY"
# {"answers":[]}                                    (nobody has answered yet)
# {"answers":[{"recipient":"ali","answer":"Yes","answered_at":"2026-10-04T09:12:44.123Z"}]}
```

How answers behave, and why it matters for your code:

- **Reading makes it final.** Until your app reads an answer, the person can
  change it (tap another button, reply again). The first read that returns it
  locks it. So read when you're ready to act on it, and act on what you got.
  An empty list locks nothing; polling while waiting is fine.
- **One entry per person** who answered, under their real username (`ali`),
  whatever name you sent to. If you asked several people, decide up front
  whether the first answer wins or you wait for everyone.
- **Poll gently**: every 10-30 seconds is plenty (the tool uses 20); people
  answer in minutes or hours. Decide what to do if nobody answers (a deadline,
  then a safe default or a reminder) instead of waiting forever.
- **A typed answer is untrusted text** (up to 4000 chars). Validate it before
  acting; never run it as a command or splice it into code or SQL.
- With buttons, `answer` is exactly one of your `options` strings, so compare
  with your own constants.
- Only your app can read its messages' answers (another app's message is
  `404`). Answers are deleted with the message's content after the retention
  period (30 days by default); after that the person can't answer either.

## Check delivery

```bash
curl -sS "$RELAY_URL/v1/messages/$MESSAGE_ID" -H "Authorization: Bearer $RELAY_API_KEY"
```

`status` is `queued`, `sending`, `delivered`, `partially_delivered` or
`failed`. `deliveries[]` shows each attempt's `status`, `attempts` and
`last_error`. Relay makes 5 attempts over about 13 minutes before marking a
delivery `failed`. Content is never returned here.
`GET /v1/messages?status=failed&limit=20` lists your app's recent messages,
newest first (`limit` 1-100, `since` an RFC 3339 time); pass the returned
`next_cursor` as `cursor` for the next page.

To see what a message would look like without sending it, POST the same body to
`/v1/preview`. It returns the rendered Telegram parts and doesn't check
recipients.

## Errors and retries

Errors look like `{"error": {"code": "...", "message": "..."}}`.

| Status | Code | What to do |
|---|---|---|
| 400 | `invalid_json` | Fix the JSON. Unknown fields are rejected, so check spelling |
| 401 | `unauthorized` | The key is missing, wrong or revoked; ask the admin |
| 404 | `not_found` | No such message, or it belongs to another app |
| 413 | `too_large` | Body over 7 MB; shrink the image or file |
| 422 | `invalid_request` | `problems` lists each issue by path, e.g. `to[1]: unknown recipient`, `to[0]: recipient has no linked channel to send on`, `blocks[0].options[2]: duplicate`. Fix exactly those |
| 429 | `rate_limited` | Too many failed logins from your IP; wait `Retry-After` seconds |
| 5xx / network error | | Retry with backoff, using the same `idempotency_key` |

**Retries:** give each logical notification an `idempotency_key` (e.g.
`backup-nas-2026-10-03`). Posting the same key again returns `200` with the
original message instead of sending a second one, so retrying after a timeout
is always safe. The tool does this for its own retries; pass `--key` to make
retries across separate runs safe too.

## Privacy

Messages end up in someone's Telegram chat. Relay encrypts content at rest and
deletes it after the retention period, but Telegram keeps the chat. So:

- Never put passwords, API keys (including `RELAY_API_KEY`), tokens or other
  secrets in a message. Say where to find them instead.
- Include only the personal data the reader needs. That goes for files
  too: don't attach whole exports, `.env` files or logs full of tokens, and
  remember the file name is shown as well.
- Relay never echoes your content in errors or logs; don't log the message
  bodies you send or the answers you get either. Log message IDs.
