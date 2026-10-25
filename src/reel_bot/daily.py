"""The daily run: ali picks a story in Relay, Tara reads it in Relay, and the
finished video goes back to her there to upload by hand.

`start` (once a morning, from a timer) makes a video and asks ali to pick one of
three stories. `tick` (every few minutes) moves every open video on: the pick is
recorded with who made it and the script is written; Tara gets the script with a
voice-note question and one message per picture prompt, which she answers with
the picture she made in ChatGPT; each answer is saved as it arrives, and once
the take and every picture are in, the video is rendered and the MP4 is sent to
her and to ali. Each step's Relay message ID is stored, so a tick is safe to
rerun: it never asks twice or sends twice. A failure stops the video and
alerts ali; `retry` reopens it. Relay being unreachable while polling for an
answer is not a failure: the next tick tries again.
"""

import logging
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from reel_bot import (
    pipeline,
    store,
    store_daily,
    store_videos,
    stories,
    videos,
    visuals,
    visuals_write,
    web,
)
from reel_bot.relay_api import (
    Answer,
    RelayError,
    alert_message,
    file_message,
    options,
    pick_message,
    picture_message,
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
            visuals.VisualsError,
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
    if "take" not in sent:
        pipeline.advance(conn, vid, None, ctx)  # the script
    asked = _ask_tara(run, vid, title, sent)
    if asked:
        return f"asked for {asked}"
    done = pipeline.advance(conn, vid, _collect(run, vid, sent), ctx)
    if done.render is None:
        return f"waiting for {done.waiting}"
    to = [run.people.narrator, run.people.picker]
    mid = run.relay.send(file_message(to, vid, title, done.render))
    store_daily.add_step(conn, vid, "sent", mid, run.clock())
    return f"sent {done.render} (${done.cost_usd():.2f})"


def _ask_tara(run: Run, vid: str, title: str, sent: dict[str, str]) -> str:
    """Send Tara whatever she hasn't been asked yet: the script to read, then one
    message per picture prompt. Returns what was asked, "" when nothing was."""
    conn, out, asked = run.conn, run.context(vid).out_dir, list[str]()
    if "take" not in sent:
        spoken = (videos.video_dir(out, vid) / pipeline.READ_FILE).read_text(encoding="utf-8")
        mid = run.relay.send(take_message(run.people.narrator, vid, title, spoken.strip()))
        store_daily.add_step(conn, vid, "take", mid, run.clock())
        asked.append("the take")
    prompts = [p.prompt for p in visuals_write.plan(conn, out, vid).pictures]
    for n in range(1, len(prompts) + 1):
        if f"picture-{n}" not in sent:
            msg = picture_message(run.people.narrator, vid, title, prompts, n)
            store_daily.add_step(conn, vid, f"picture-{n}", run.relay.send(msg), run.clock())
            asked.append(f"picture {n}")
    return ", ".join(asked)


def _collect(run: Run, vid: str, sent: dict[str, str]) -> Path | None:
    """Save every picture Tara has sent; returns her take when it has just arrived."""
    conn, out = run.conn, run.context(vid).out_dir
    folder = videos.video_dir(out, vid)
    for n, pic in enumerate(visuals_write.plan(conn, out, vid).pictures, 1):
        if visuals_write.found(folder, pic) is None and (got := _poll(run, sent[f"picture-{n}"])):
            if got.image is None:
                raise DailyError(f"the answer to picture {n} has no picture")
            made = visuals_write.Made(got.image, got.recipient, run.clock())
            visuals_write.add(conn, out, vid, n, made)
    if "voice" in store_videos.stage_runs(conn, vid) or not (got := _poll(run, sent["take"])):
        return None
    if got.audio is None:
        raise DailyError("the take's answer has no audio")
    take = folder / f"take{got.audio_suffix()}"
    take.write_bytes(got.audio)
    return take


def _where(conn: sqlite3.Connection, vid: str) -> str:
    sent, runs = store_daily.steps(conn, vid), store_videos.stage_runs(conn, vid)
    if store_videos.get_video(conn, vid).story_id is None:
        return "story pick"
    if "take" not in sent:
        return "script"
    if "voice" in runs and "visuals" in runs:
        return "render and send"
    return "the take and pictures"


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
