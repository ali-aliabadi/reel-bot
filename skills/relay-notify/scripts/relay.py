#!/usr/bin/env python3
"""Notify a person through Relay, ask them a question and read the answer.

Standard library only (Python 3.8+). Use it from the shell:

    relay.py notify "Backup finished in 4m12s"
    relay.py notify --urgency high --title "Backup failed" --field Host=nas "Disk full"
    relay.py notify "September invoice" --file invoice.pdf --file-caption "Due Oct 15"
    relay.py ask "Ship v2.3 to production?" --option Yes --option No --wait 3600
    relay.py answer msg_01J... --wait 600
    relay.py send < message.json          # any POST /v1/messages body
    relay.py status msg_01J...
    relay.py recipients
    relay.py check --to ali               # config, key and recipient all good?

or import it: notify(), ask(), wait_for_answer(), send(), status(), recipients(),
check(), file_block().

Configuration comes from the environment (see SKILL.md):
    RELAY_URL      Relay's base URL, e.g. https://relay.alialiabadi.ir (required)
    RELAY_API_KEY  this app's API key (required; never print or log it)
    RELAY_APP      this app's name, sent as every message's source (required)
    RELAY_USER     who to notify when no --to is given (optional; with neither,
                   sending fails: there is no default recipient)

Exit codes: 0 ok, 1 Relay or network error, 2 bad usage or config (including
no recipient), 3 nobody answered before --wait ran out, 4 check found a
recipient that is unknown or can't receive yet.
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

NO_ANSWER = 3
CHECK_FAILED = 4
MAX_FILE_BYTES = 45 << 20  # Relay's limit for one file block; over 5 MB needs a raised RELAY_MAX_BODY_BYTES


class RelayError(Exception):
    """A Relay API error. Relay's messages never contain your content."""

    def __init__(self, status, code, message, problems=None):
        self.status, self.code, self.problems = status, code, problems or []
        detail = "; ".join(self.problems) or message
        super().__init__(f"relay: {status} {code}: {detail}")


class ConfigError(RelayError):
    """Missing setup the caller must supply, such as who to notify."""

    def __init__(self, message):
        super().__init__(0, "config", message)
        self.args = (f"relay: {message}",)

    def __str__(self):
        return self.args[0]


def _config():
    url = os.environ.get("RELAY_URL", "").strip().rstrip("/")
    key = os.environ.get("RELAY_API_KEY", "").strip()
    app = os.environ.get("RELAY_APP", "").strip()
    if not url or not key or not app:
        print("relay: set RELAY_URL, RELAY_API_KEY and RELAY_APP (ask the user for them)", file=sys.stderr)
        sys.exit(2)
    return url, key, app


def user():
    """This app's recipient from $RELAY_USER, or None. There is no default."""
    return os.environ.get("RELAY_USER", "").strip() or None


def _to(to):
    """The recipients to send to: to if given, else [$RELAY_USER]."""
    if to:
        return list(to)
    if user():
        return [user()]
    raise ConfigError("say who to notify: pass --to NAME (or to=[...]) or set RELAY_USER; "
                      "run 'relay.py recipients' to see who exists")


def _request(method, path, body=None, retries=3):
    """Calls Relay. 5xx and network errors are retried with backoff; callers
    make POSTs safe to retry by always sending an idempotency_key."""
    url, key, _ = _config()
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "relay-notify-skill",
        })
        try:
            big = len(data or b"") >= (1 << 20) or path.endswith("/answers")
            timeout = 300 if big else 15  # room to upload a file or fetch a voice answer
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise _api_error(e) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RelayError(0, "network", str(getattr(e, "reason", e))) from None
    raise AssertionError("unreachable")


def _api_error(e):
    try:
        err = json.load(e).get("error", {})
    except (ValueError, AttributeError):
        err = {}
    return RelayError(e.code, err.get("code", "http_error"), err.get("message", e.reason), err.get("problems"))


def send(body):
    """POSTs a full /v1/messages body. Fills in to (RELAY_USER), source
    (RELAY_APP) and idempotency_key when missing; with no to and no
    RELAY_USER it raises ConfigError. Returns {"id", "status"}."""
    body = dict(body)
    body["to"] = _to(body.get("to"))
    body.setdefault("source", _config()[2])
    body.setdefault("idempotency_key", "auto-" + uuid.uuid4().hex)
    return _request("POST", "/v1/messages", body)


def notify(text=None, title=None, urgency="normal", to=None, fields=None, blocks=None,
           key=None, file=None, file_caption=None):
    """Sends a notification. fields is a dict or list of (label, value) pairs;
    blocks are extra content blocks appended after the text and fields; file
    is a path to attach (see file_block)."""
    out = []
    if text:
        out.append({"type": "text", "text": text})
    if fields:
        items = fields.items() if isinstance(fields, dict) else fields
        out.append({"type": "fields", "items": [{"label": k, "value": str(v)} for k, v in items]})
    out.extend(blocks or [])
    if file:
        out.append(file_block(file, caption=file_caption))
    return send(_envelope(out, title, urgency, to, key))


def file_block(path, caption=None, filename=None, content_type=None):
    """Builds a file block from a local file (one per message, up to 5 MB, or 45 MB
    when the admin has raised RELAY_MAX_BODY_BYTES).
    The recipient sees filename (default: the file's own name), so make sure
    it gives nothing away the reader shouldn't see."""
    size = os.path.getsize(path)
    if size == 0 or size > MAX_FILE_BYTES:
        raise RelayError(0, "file", f"file must be 1 byte to {MAX_FILE_BYTES} bytes, got {size}")
    with open(path, "rb") as f:
        data = f.read()
    block = {
        "type": "file",
        "filename": filename or os.path.basename(path),
        "content_type": content_type or mimetypes.guess_type(path)[0] or "application/octet-stream",
        "base64": base64.b64encode(data).decode("ascii"),
    }
    if caption:
        block["caption"] = caption
    return block


def ask(question, options=None, text=None, title=None, urgency="normal", to=None,
        webhook=None, key=None, voice=False):
    """Asks a question: buttons with options, a voice note with voice=True,
    otherwise a typed reply. Returns the message ID to pass to wait_for_answer()."""
    out = [{"type": "text", "text": text}] if text else []
    q = {"type": "question", "text": question}
    if options:
        q["options"] = list(options)
    if voice:
        q["reply"] = "voice"
    if webhook:
        q["webhook"] = webhook
    out.append(q)
    return send(_envelope(out, title, urgency, to, key))["id"]


def _envelope(blocks, title, urgency, to, key):
    body = {"to": _to(to), "urgency": urgency, "blocks": blocks}
    for name, value in (("title", title), ("idempotency_key", key)):
        if value:
            body[name] = value
    return body


def answers(message_id):
    """Returns the answers so far. Every answer returned becomes final."""
    return _request("GET", f"/v1/messages/{message_id}/answers")["answers"]


def wait_for_answer(message_id, timeout_s=3600, every_s=20):
    """Polls until someone answers and returns the first answer
    ({"recipient", "answer", "answered_at"}, plus "audio": {"content_type",
    "base64"} for a voice note), or None after timeout_s."""
    deadline = time.monotonic() + timeout_s
    while True:
        got = answers(message_id)
        if got:
            return got[0]
        if time.monotonic() + every_s > deadline:
            return None
        time.sleep(every_s)


def status(message_id):
    """Delivery status of a message (never its content)."""
    return _request("GET", f"/v1/messages/{message_id}")


def recipients():
    """Who can be notified: usernames, aliases and linked channels."""
    return _request("GET", "/v1/recipients")["recipients"]


def check(to=None):
    """Checks the setup without sending anything: RELAY_URL and RELAY_API_KEY
    work, and each name in to (default RELAY_USER) is a recipient with a
    linked channel. Returns {"ok", "app", "checked", "problems", "recipients"}.
    Raises RelayError when Relay can't be reached or rejects the key."""
    _, _, app = _config()
    rs = recipients()
    names = list(to) if to else ([user()] if user() else [])
    by_name = {}
    for r in rs:
        for n in [r["username"], *r.get("aliases", [])]:
            by_name[n] = r
    checked, problems = [], []
    for n in names:
        r = by_name.get(n)
        if r is None:
            problems.append(f"{n}: unknown recipient")
            continue
        if not r.get("linked_channels"):
            problems.append(f"{n}: no linked channel yet, so nothing can reach them")
        checked.append({"name": n, "username": r["username"], "linked_channels": r.get("linked_channels", [])})
    if not names:
        problems.append("no recipient to check: pass --to NAME or set RELAY_USER")
    available = [{"username": r["username"], "aliases": r.get("aliases", []),
                  "display_name": r.get("display_name", ""),
                  "linked_channels": r.get("linked_channels", [])} for r in rs]
    return {"ok": not problems, "app": app, "checked": checked, "problems": problems,
            "recipients": available}


def _parse_fields(pairs):
    out = []
    for p in pairs or []:
        label, sep, value = p.partition("=")
        if not sep:
            print("relay: --field needs Label=value", file=sys.stderr)
            sys.exit(2)
        out.append((label, value))
    return out


def _cli(argv):
    p = argparse.ArgumentParser(prog="relay.py", description="Notify a person through Relay.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--to", action="append", help="recipient username or alias (repeatable; default $RELAY_USER, required without it)")
        sp.add_argument("--urgency", default="normal", choices=["low", "normal", "high", "critical"])
        sp.add_argument("--title")
        sp.add_argument("--key", help="idempotency key; reuse it when retrying the same notification")

    n = sub.add_parser("notify", help="send a notification")
    n.add_argument("text", nargs="?")
    n.add_argument("--field", action="append", metavar="LABEL=VALUE", help="a fields line (repeatable)")
    n.add_argument("--file", metavar="PATH", help="attach a file (up to 5 MB, 45 MB with a raised body limit), sent as a Telegram document")
    n.add_argument("--file-caption", help="caption shown under the file")
    common(n)

    a = sub.add_parser("ask", help="ask a question; prints the message ID, or the answer with --wait")
    a.add_argument("question")
    a.add_argument("--option", action="append", help="a button (repeatable); none means a typed reply")
    a.add_argument("--voice", action="store_true", help="ask for a voice note instead of text")
    a.add_argument("--save-audio", metavar="PATH", help="with --wait: write a voice-note answer here")
    a.add_argument("--text", help="context shown above the question")
    a.add_argument("--webhook", help="public https URL nudged when an answer arrives")
    a.add_argument("--wait", type=int, metavar="SECONDS", help="wait this long for the answer")
    a.add_argument("--every", type=int, default=20, metavar="SECONDS", help="poll interval (default 20)")
    common(a)

    w = sub.add_parser("answer", help="read the answer to a question (reading makes it final)")
    w.add_argument("message_id")
    w.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="poll this long for an answer")
    w.add_argument("--every", type=int, default=20, metavar="SECONDS")
    w.add_argument("--save-audio", metavar="PATH", help="write a voice-note answer here")

    sub.add_parser("send", help="send a full POST /v1/messages JSON body read from stdin")
    sub.add_parser("recipients", help="list recipients")
    c = sub.add_parser("check", help="check config, API key and recipient without sending anything")
    c.add_argument("--to", action="append", help="recipient to check (repeatable; default $RELAY_USER)")
    s = sub.add_parser("status", help="delivery status of a message")
    s.add_argument("message_id")

    args = p.parse_args(argv)
    if args.cmd == "notify":
        if not args.text and not args.field and not args.file:
            p.error("notify needs text, --field or --file")
        if args.file_caption and not args.file:
            p.error("--file-caption needs --file")
        return notify(args.text, args.title, args.urgency, args.to, _parse_fields(args.field),
                      key=args.key, file=args.file, file_caption=args.file_caption)
    if args.cmd == "ask":
        if args.voice and args.option:
            p.error("--voice can't have --option")
        mid = ask(args.question, args.option, args.text, args.title, args.urgency, args.to,
                  args.webhook, args.key, args.voice)
        if args.wait is None:
            return {"id": mid}
        return _waited(mid, args.wait, args.every, args.save_audio)
    if args.cmd == "answer":
        return _waited(args.message_id, args.wait, args.every, args.save_audio)
    if args.cmd == "send":
        return send(json.load(sys.stdin))
    if args.cmd == "status":
        return status(args.message_id)
    if args.cmd == "check":
        res = check(args.to)
        if not res["ok"]:
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(CHECK_FAILED)
        return res
    return {"recipients": recipients()}


def _waited(message_id, wait, every, save_audio=None):
    got = wait_for_answer(message_id, wait, every)
    if got is None:
        print(json.dumps({"id": message_id, "answer": None}))
        sys.exit(NO_ANSWER)
    audio = got.pop("audio", None)
    if audio and save_audio:
        with open(save_audio, "wb") as f:
            f.write(base64.b64decode(audio["base64"]))
        got["audio"] = {"content_type": audio["content_type"], "saved_to": save_audio}
    elif audio:
        got["audio"] = audio
    return {"id": message_id, **got}


def main():
    try:
        print(json.dumps(_cli(sys.argv[1:]), ensure_ascii=False))
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except RelayError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print("relay: stdin is not valid JSON", file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"relay: can't read file: {e.strerror}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
