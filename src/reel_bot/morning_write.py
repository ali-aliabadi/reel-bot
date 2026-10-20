"""The morning pick, I/O edge: ask ali through Relay, then record the story ali taps.

`run` is safe to call as often as a timer likes. Each call does the first of:
1. If a pick question is open, read its answer: record the pick (the tale is
   spent and the pick names who tapped it), close it on "Skip today", or keep
   waiting. A question unanswered for 20 hours expires; nothing is picked for ali.
2. With `ask` off (`reel-bot morning --check`, for a frequent timer), stop.
3. If a question already went out today (UTC), do nothing.
4. Otherwise make a video with three candidates from unused tales and send the
   question. The idempotency key is the video ID, so a run that stopped after
   sending resends to the same Relay message instead of asking twice.
"""

import logging
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from reel_bot import morning, relay_api, store, store_relay, store_videos, stories, videos

log = logging.getLogger(__name__)


class Relay(Protocol):
    """What the morning run needs from Relay (relay_api.Client, or a fake in tests)."""

    def send(self, body: dict[str, object], key: str) -> str: ...

    def answers(self, message_id: str) -> tuple[relay_api.Answer, ...]: ...


@dataclass(frozen=True)
class Context:
    relay: Relay
    to: str  # who picks (RELAY_USER)
    clock: Callable[[], datetime]
    rng: random.Random


def _options(conn: sqlite3.Connection, video: videos.Video) -> list[stories.Story]:
    by_id = {s.id: s for s in store.all_stories(conn)}
    return [by_id[i] for i in video.story_options]


def _check(conn: sqlite3.Connection, m: store_relay.RelayMessage, ctx: Context) -> str | None:
    """Settle one open question; a line to report, or None when it expired."""
    video = store_videos.get_video(conn, m.video_id)
    now = ctx.clock()
    if video.story_id is not None:  # recorded by a run that stopped before closing
        store_relay.close_message(conn, m.message_id, "picked", now)
        return f"{video.id}: picked {video.story_id}"
    d = morning.decide(ctx.relay.answers(m.message_id), _options(conn, video))
    if d is None:
        if now - m.sent_at < morning.EXPIRE_AFTER:
            return f"{video.id}: waiting for the pick"
        store_relay.close_message(conn, m.message_id, "expired", now)
        log.info("pick expired", extra={"video_id": video.id, "message_id": m.message_id})
        return None
    outcome = d.outcome
    if d.story_id is not None and d.by is not None:
        try:
            store_videos.choose_story(conn, video.id, d.story_id, d.by, now)
        except store.StoreError:  # e.g. the tale was spent by hand meanwhile
            outcome = "refused"
            log.warning("pick refused", extra={"video_id": video.id, "story_id": d.story_id})
    store_relay.close_message(conn, m.message_id, outcome, now)
    log.info(f"pick {outcome}", extra={"video_id": video.id, "message_id": m.message_id})
    return f"{video.id}: {outcome}" + (f" {d.story_id} by {d.by}" if d.story_id else "")


def _new_video(conn: sqlite3.Connection, ctx: Context) -> videos.Video:
    now = ctx.clock()
    left = store_relay.unasked_video(conn, morning.PURPOSE)
    if left is not None and store_videos.get_video(conn, left).created_at.date() == now.date():
        return store_videos.get_video(conn, left)
    used = store.used_tales(conn)
    picks = stories.candidates(store.all_stories(conn), used, morning.CHOICES, ctx.rng)
    if not picks:
        raise morning.MorningError("every tale in the catalog has been told")
    framing, length = morning.settings(store_relay.picked_count(conn))
    video = videos.Video(videos.new_id(now), now, framing, length)
    store_videos.add_video(conn, video)
    store_videos.set_options(conn, video.id, [s.id for s in picks])
    return store_videos.get_video(conn, video.id)


def run(conn: sqlite3.Connection, ctx: Context, *, ask: bool = True) -> str:
    """One step of the morning pick; returns a line saying what happened."""
    for m in store_relay.open_messages(conn, morning.PURPOSE):
        line = _check(conn, m, ctx)
        if line is not None:
            return line
    if not ask:
        return "no open question"
    last = store_relay.last_sent(conn, morning.PURPOSE)
    if last is not None and last.sent_at.date() == ctx.clock().date():
        return f"{last.video_id}: already asked today"
    video = _new_video(conn, ctx)
    body = morning.question(ctx.to, video, _options(conn, video))
    message_id = ctx.relay.send(body, f"pick-{video.id}")
    store_relay.add_message(
        conn, store_relay.RelayMessage(message_id, video.id, morning.PURPOSE, ctx.clock())
    )
    return f"{video.id}: asked {ctx.to} to pick ({video.framing}, {video.length})"
