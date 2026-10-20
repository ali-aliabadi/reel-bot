"""Relay client (skills/relay-notify): send a message, read the answers to its question.

Stdlib HTTP through `web.Net`, so tests drive it with a fake network. Every send
carries an idempotency key, so a retry after a lost reply never sends twice.
Message bodies and answers are never logged; only message IDs are.
"""

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from reel_bot import web

log = logging.getLogger(__name__)

TIMEOUT_S = 30.0
MAX_ANSWER = 4000  # Relay's limit for a typed answer


class RelayError(Exception):
    pass


@dataclass(frozen=True)
class Answer:
    recipient: str  # the person's real username, whatever name the message was sent to
    answer: str  # a button's exact text, or untrusted typed text
    answered_at: datetime


@dataclass(frozen=True)
class Client:
    url: str  # https base URL, no trailing slash
    api_key: str
    app: str  # sent as every message's source
    net: web.Net

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def send(self, body: dict[str, object], key: str) -> str:
        """Send a message; return its ID. `key` makes retries and reruns send it once."""
        payload = {**body, "source": self.app, "idempotency_key": key}
        req = web.Request(
            "POST", f"{self.url}/v1/messages", "Relay send", json.dumps(payload).encode(),
            self._headers(),
        )  # fmt: skip
        try:
            reply = self.net.send(req)
        except web.WebError as err:
            raise RelayError(str(err)) from err
        message_id = parse_sent(reply.body)
        log.info("relay message sent", extra={"message_id": message_id})
        return message_id

    def answers(self, message_id: str) -> tuple[Answer, ...]:
        """The answers so far. Reading one makes it final, so read when ready to act."""
        if not message_id.startswith("msg_") or not (
            message_id[4:].isascii() and message_id[4:].isalnum()
        ):
            raise RelayError(f"not a Relay message ID: {message_id!r}")
        req = web.Request(
            "GET", f"{self.url}/v1/messages/{message_id}/answers", "Relay answers",
            headers=self._headers(),
        )  # fmt: skip
        try:
            reply = self.net.send(req)
        except web.WebError as err:
            raise RelayError(str(err)) from err
        return parse_answers(reply.body)


def parse_sent(raw: bytes) -> str:
    try:
        message_id = cast(dict[str, Any], json.loads(raw))["id"]
    except (ValueError, KeyError, TypeError, RecursionError) as err:
        raise RelayError("Relay's reply has no message ID") from err
    if not isinstance(message_id, str) or not message_id.startswith("msg_"):
        raise RelayError("Relay's reply has no message ID")
    return message_id


def _answer(item: object) -> Answer:
    entry = cast(dict[str, Any], item)
    who, text, at = entry["recipient"], entry["answer"], entry["answered_at"]
    if not (isinstance(who, str) and who and isinstance(text, str) and isinstance(at, str)):
        raise TypeError("answer fields")
    when = datetime.fromisoformat(at)
    if when.tzinfo is None or not math.isfinite(when.timestamp()):
        raise ValueError("answered_at needs a time zone")
    return Answer(who, text[:MAX_ANSWER], when)


def parse_answers(raw: bytes) -> tuple[Answer, ...]:
    try:
        items = cast(Sequence[object], cast(dict[str, Any], json.loads(raw))["answers"])
        return tuple(_answer(item) for item in items)  # a non-list fails in _answer
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as err:
        raise RelayError("Relay's answers reply is not readable") from err
