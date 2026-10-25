"""What the parts of the daily run share (see `daily`): the run's database, Relay
and people, its error, and polling Relay for an answer."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from reel_bot import pipeline, web
from reel_bot.relay_api import Answer

log = logging.getLogger(__name__)


class DailyError(Exception):
    pass


class Messenger(Protocol):
    def send(self, message: dict[str, object]) -> str: ...

    def answers(self, message_id: str) -> list[Answer]: ...


@dataclass(frozen=True)
class People:
    picker: str  # Relay username of who picks the story (ali)
    narrator: str  # Relay username of who reads it and uploads the video (Tara)


@dataclass(frozen=True)
class Run:
    conn: sqlite3.Connection
    relay: Messenger
    people: People
    clock: Callable[[], datetime]
    context: Callable[[str], pipeline.Context]  # a video's stage context (its budget)


def poll(run: Run, message_id: str) -> Answer | None:
    """A message's answer; None while there is none or Relay is unreachable (the
    next tick asks again)."""
    try:
        got = run.relay.answers(message_id)
    except web.WebError as err:
        log.warning("relay unreachable", extra={"message_id": message_id, "detail": str(err)})
        return None
    return got[0] if got else None
