"""The daily run: ali picks a story in Relay, Tara reads it in Relay, and the
finished video goes back to her there to upload by hand.

`start` (once a morning, from a timer) makes a video and asks ali to pick one of
three stories. `tick` (every few minutes) moves every open video on: the pick is
recorded with who made it, then Tara is guided one step at a time (`daily_tara`):
the script from ChatGPT, her voice note, each picture. Once the take and every
picture are in, the video is rendered and the MP4 is sent to her and to ali.

Everything a tick needs is in the database (`daily_steps`, `stage_runs`,
`daily_errors`) and on disk, and every Relay message has an idempotency key, so
a tick can stop anywhere and the next one carries on: it never asks or sends
twice. An error is retried on the next tick; after MAX_ERRORS in a row the video
stops and the picker gets an alert (until it is sent, the video stays open and
the next tick tries again), and `retry` reopens it. Relay being unreachable while
polling for an answer is not an error.
"""

import logging
import random
import sqlite3

from reel_bot import (
    pipeline,
    store,
    store_daily,
    store_videos,
    stories,
    videos,
    visuals,
    web,
)
from reel_bot.daily_run import DailyError, Run, poll
from reel_bot.daily_tara import ask_take, collect, next_picture, script_step
from reel_bot.relay_api import (
    Answer,
    RelayError,
    alert_message,
    file_message,
    options,
    pick_message,
)

log = logging.getLogger(__name__)

CHOICES = 3
MAX_ERRORS = 3  # errors in a row (one per tick) before a video stops and alerts
ERRORS = (
    *pipeline.ERRORS,
    DailyError,
    OSError,
    RelayError,
    store.StoreError,
    videos.VideoError,
    visuals.VisualsError,
    web.WebError,
)


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
        except ERRORS as err:
            status = _error(run, vid, err)
        else:
            store_daily.clear_errors(run.conn, vid)
        lines.append(f"{vid}\t{status}")
    return lines


def retry(conn: sqlite3.Connection, video_id: str) -> None:
    """Reopen a failed video so the next tick carries on from where it stopped."""
    if "failed" not in store_daily.steps(conn, video_id):
        raise DailyError(f"video {video_id} has not failed")
    store_daily.clear_step(conn, video_id, "failed")
    store_daily.clear_errors(conn, video_id)


def _story(conn: sqlite3.Connection, story_id: str) -> stories.Story:
    return next(s for s in store.all_stories(conn) if s.id == story_id)


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
        got = poll(run, sent["pick"])
        if got is None:
            return "waiting for the pick"
        story_id = _picked(run, video, got)
        store_videos.choose_story(conn, vid, story_id, got.recipient, run.clock())
        video = store_videos.get_video(conn, vid)
    story = _story(conn, str(video.story_id))
    if "script" not in store_videos.stage_runs(conn, vid):
        waiting = script_step(run, video, story, sent)
        if waiting:
            return waiting
    if "take" not in sent:
        pipeline.advance(conn, vid, None, ctx)  # read-this.txt from the script
        return ask_take(run, vid, story.title)
    collect(run, vid, story.title, sent)
    done = pipeline.advance(conn, vid, None, ctx)
    if done.render is None:
        asked = next_picture(run, vid, story.title, sent)
        return f"asked for picture {asked}" if asked else f"waiting for {done.waiting}"
    to = [run.people.narrator, run.people.picker]
    mid = run.relay.send(file_message(to, vid, story.title, done.render))
    store_daily.add_step(conn, vid, "sent", mid, run.clock())
    return f"sent {done.render} (${done.cost_usd():.2f})"


def _where(conn: sqlite3.Connection, vid: str) -> str:
    sent, runs = store_daily.steps(conn, vid), store_videos.stage_runs(conn, vid)
    if store_videos.get_video(conn, vid).story_id is None:
        return "story pick"
    if "script" not in runs or "take" not in sent:
        return "script"
    if "voice" in runs and "visuals" in runs:
        return "render and send"
    return "the take and pictures"


def _error(run: Run, vid: str, err: Exception) -> str:
    """Count an error in a row; the next tick tries again until there are
    MAX_ERRORS, then the video stops once the picker has been alerted."""
    now, where = run.clock(), _where(run.conn, vid)
    count = store_daily.add_error(run.conn, vid, now)
    extra = {"video_id": vid, "step": where, "errors": count, "detail": str(err)}
    if count < MAX_ERRORS:
        log.warning("daily run error; the next tick tries again", extra=extra)
        return f"error {count} of {MAX_ERRORS}, trying again next tick: {err}"
    log.error("daily run failed", extra=extra)
    try:
        alert = alert_message(run.people.picker, vid, where, str(err), now.isoformat())
        mid = run.relay.send(alert)
    except (RelayError, web.WebError) as alert_err:
        log.exception("alert not sent", extra={"video_id": vid, "detail": str(alert_err)})
        return f"failed, alert not sent yet (the next tick tries again): {err}"
    store_daily.add_step(run.conn, vid, "failed", mid, now)
    store_daily.clear_errors(run.conn, vid)
    return f"failed: {err}"
