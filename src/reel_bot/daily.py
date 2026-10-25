"""The daily run: ali picks a story in Relay, Tara reads it in Relay, and the
finished video goes back to her there to upload by hand.

`start` (once a morning, from a timer) makes a video and asks ali to pick one of
three stories. `tick` (every few minutes) moves every open video one step on:
the pick is recorded with who made it, the script and pictures are made, Tara
gets the script and a voice-note question, her take is rendered and the MP4 is
sent to her and to ali. Each step's Relay message ID is stored, so a tick is
safe to rerun: it never asks twice or sends twice. A failure stops the video and
alerts ali; `retry` reopens it. Relay being unreachable while polling for an
answer is not a failure: the next tick tries again.
"""

import logging
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from reel_bot import pipeline, store, store_daily, store_videos, stories, videos, web
from reel_bot.relay_api import (
    Answer,
    RelayError,
    alert_message,
    file_message,
    options,
    pick_message,
    take_message,
)

log = logging.getLogger(__name__)

CHOICES = 3


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


def start(run: Run, framing: str, length: str, rng: random.Random) -> str | None:
    """Make today's video and ask for the pick; None while an earlier pick is unanswered."""
    conn = run.conn
    for vid in store_daily.open_videos(conn):
        if store_videos.get_video(conn, vid).story_id is None:
            log.info("still waiting for a pick", extra={"video_id": vid})
            return None
    picks = stories.candidates(store.all_stories(conn), store.used_tales(conn), CHOICES, rng)
    if not picks:
        raise DailyError("every tale in the catalog has been told")
    now = run.clock()
    video = videos.Video(videos.new_id(now), now, framing, length)
    store_videos.add_video(conn, video)
    store_videos.set_options(conn, video.id, [s.id for s in picks])
    mid = run.relay.send(pick_message(run.people.picker, video.id, picks))
    store_daily.add_step(conn, video.id, "pick", mid, run.clock())
    log.info("asked for the pick", extra={"video_id": video.id, "message_id": mid})
    return video.id


def tick(run: Run) -> list[str]:
    """Move every open video on by what is ready; one status line per video."""
    lines: list[str] = []
    for vid in store_daily.open_videos(run.conn):
        try:
            status = _advance(run, vid)
        except (
            *pipeline.ERRORS,
            DailyError,
            OSError,
            RelayError,
            store.StoreError,
            videos.VideoError,
            web.WebError,
        ) as err:
            _fail(run, vid, err)
            status = f"failed: {err}"
        lines.append(f"{vid}\t{status}")
    return lines


def retry(conn: sqlite3.Connection, video_id: str) -> None:
    """Reopen a failed video so the next tick carries on from where it stopped."""
    if "failed" not in store_daily.steps(conn, video_id):
        raise DailyError(f"video {video_id} has not failed")
    store_daily.clear_step(conn, video_id, "failed")


def _story(conn: sqlite3.Connection, story_id: str) -> stories.Story:
    return next(s for s in store.all_stories(conn) if s.id == story_id)


def _poll(run: Run, message_id: str) -> Answer | None:
    try:
        got = run.relay.answers(message_id)
    except web.WebError as err:
        log.warning("relay unreachable", extra={"message_id": message_id, "detail": str(err)})
        return None
    return got[0] if got else None


def _picked(run: Run, video: videos.Video, got: Answer) -> str:
    offered = [_story(run.conn, sid) for sid in video.story_options]
    labels = options(offered)
    if got.text not in labels:
        raise DailyError("the pick is not one of the offered stories")
    return offered[labels.index(got.text)].id


def _advance(run: Run, vid: str) -> str:
    conn, ctx = run.conn, run.context(vid)
    video = store_videos.get_video(conn, vid)
    sent = store_daily.steps(conn, vid)
    if video.story_id is None:
        got = _poll(run, sent["pick"])
        if got is None:
            return "waiting for the pick"
        story_id = _picked(run, video, got)
        store_videos.choose_story(conn, vid, story_id, got.recipient, run.clock())
        video = store_videos.get_video(conn, vid)
    title = _story(conn, str(video.story_id)).title
    folder = videos.video_dir(ctx.out_dir, vid)
    if "take" not in sent:
        pipeline.advance(conn, vid, None, ctx)  # the script and the pictures
        spoken = (folder / pipeline.READ_FILE).read_text(encoding="utf-8").strip()
        mid = run.relay.send(take_message(run.people.narrator, vid, title, spoken))
        store_daily.add_step(conn, vid, "take", mid, run.clock())
        return "asked for the take"
    got = _poll(run, sent["take"])
    if got is None:
        return "waiting for the take"
    if got.audio is None:
        raise DailyError("the take's answer has no audio")
    take = folder / f"take{got.audio_suffix()}"
    take.write_bytes(got.audio)
    done = pipeline.advance(conn, vid, take, ctx)
    if done.render is None:
        raise DailyError(f"the render did not finish: waiting for {done.waiting}")
    to = [run.people.narrator, run.people.picker]
    mid = run.relay.send(file_message(to, vid, title, done.render))
    store_daily.add_step(conn, vid, "sent", mid, run.clock())
    return f"sent {done.render} (${done.cost_usd():.2f})"


def _where(conn: sqlite3.Connection, vid: str) -> str:
    sent, runs = store_daily.steps(conn, vid), store_videos.stage_runs(conn, vid)
    if store_videos.get_video(conn, vid).story_id is None:
        return "story pick"
    if "take" not in sent:
        return "script and pictures"
    return "render and send" if "voice" in runs else "the take"


def _fail(run: Run, vid: str, err: Exception) -> None:
    where, now = _where(run.conn, vid), run.clock()
    log.error("daily run failed", extra={"video_id": vid, "step": where, "detail": str(err)})
    try:
        mid = run.relay.send(
            alert_message(run.people.picker, vid, where, str(err), now.isoformat())
        )
    except (RelayError, web.WebError) as alert_err:
        log.exception("alert not sent", extra={"video_id": vid, "detail": str(alert_err)})
        mid = "unsent"
    store_daily.add_step(run.conn, vid, "failed", mid, now)
