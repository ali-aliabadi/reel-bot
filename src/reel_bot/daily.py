"""The daily run: ali picks a story in Relay, Kokoro reads it (or Tara, with
REEL_BOT_NARRATION=tara), and the finished video goes to Tara in Relay to upload
by hand.

`start` (once a morning, from a timer) makes a video and asks ali to pick one of
three stories, or "Skip today" (`daily_cancel`); a pick still unanswered from an
earlier morning is cancelled and replaced. `tick` (every few minutes) moves every
open video on: the pick is recorded with who made it, then Tara is guided one
step at a time (`daily_tara`): the script from ChatGPT, then Kokoro's reading
(or her voice note), then each picture. Once the voice and every picture are in,
the video is rendered and the MP4 is sent to her and to ali.

Everything a tick needs is in the database (`daily_steps`, `stage_runs`,
`daily_errors`) and on disk, and every Relay message has an idempotency key, so
a tick can stop anywhere and the next one carries on: it never asks or sends
twice. An error is retried on the next tick; after MAX_ERRORS in a row the video
stops and the picker gets an alert (until it is sent, the video stays open and
the next tick tries again), and `retry` reopens it. Relay being unreachable while
polling for an answer is not an error. A tick killed mid-way (out of memory, say)
can't count its own error, so each tick is also counted as it starts on a video
(`daily_running`); after MAX_ERRORS that never finished, the video stops the same way.
"""

import logging
import random
import sqlite3

from reel_bot import (
    guide,
    narration,
    pipeline,
    publish,
    script,
    script_write,
    store,
    store_daily,
    store_videos,
    stories,
    videos,
    visuals,
    web,
)
from reel_bot.daily_cancel import cancel, expire_picks
from reel_bot.daily_run import DailyError, Run, poll
from reel_bot.daily_tara import ask_take, collect, narrate, next_picture, script_step
from reel_bot.relay_api import (
    SKIP,
    Answer,
    RelayError,
    alert_message,
    file_message,
    options,
    pick_message,
)

log = logging.getLogger(__name__)

CHOICES = 3
MAX_ERRORS = 10  # errors in a row (one per tick, a minute apart) before a video stops and alerts
ERRORS = (
    *pipeline.ERRORS,
    DailyError,
    guide.GuideError,
    OSError,
    RelayError,
    store.StoreError,
    videos.VideoError,
    visuals.VisualsError,
    web.WebError,
)


def start(run: Run, framing: str, length: str, rng: random.Random) -> str | None:
    """Make today's video and ask for the pick; None while a pick asked under
    EXPIRE_AFTER ago is unanswered (an older one is cancelled first)."""
    conn = run.conn
    for vid in expire_picks(run):
        log.info("unanswered pick replaced", extra={"video_id": vid})
    for vid in store_daily.open_videos(conn):
        if store_videos.get_video(conn, vid).story_id is None:
            log.info("still waiting for a pick", extra={"video_id": vid})
            return None
    catalog, used = store.all_stories(conn), store.used_tales(conn)
    picks = stories.candidates(catalog, used, CHOICES, rng, store.offered_tales(conn))
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
        started = store_daily.begin(run.conn, vid, run.clock())
        if started > MAX_ERRORS:
            killed = f"the last {started - 1} ticks on it were killed before finishing"
            lines.append(f"{vid}\t{_stop(run, vid, killed)}")
            continue
        try:
            status = _advance(run, vid)
        except ERRORS as err:
            status = _error(run, vid, err)
        else:
            store_daily.clear_errors(run.conn, vid)
        store_daily.end(run.conn, vid)
        lines.append(f"{vid}\t{status}")
    return lines


def retry(conn: sqlite3.Connection, video_id: str) -> None:
    """Reopen a failed video so the next tick carries on from where it stopped."""
    if "failed" not in store_daily.steps(conn, video_id):
        raise DailyError(f"video {video_id} has not failed")
    store_daily.clear_step(conn, video_id, "failed")
    store_daily.clear_errors(conn, video_id)
    store_daily.end(conn, video_id)


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
        if got.text == SKIP:
            cancel(run, vid, "skipped by the picker")
            return "skipped today"
        story_id = _picked(run, video, got)
        store_videos.choose_story(conn, vid, story_id, got.recipient, run.clock())
        video = store_videos.get_video(conn, vid)
    story = _story(conn, str(video.story_id))
    if "script" not in store_videos.stage_runs(conn, vid):
        waiting = script_step(run, video, story, sent)
        if waiting:
            return waiting
    if started := _start_voice(run, vid, story.title, sent):
        return started
    collect(run, vid, story.title, sent)
    done = pipeline.advance(conn, vid, None, ctx)
    if done.render is None:
        asked = next_picture(run, vid, story.title, sent)
        return f"asked for picture {asked}" if asked else f"waiting for {done.waiting}"
    to = [run.people.narrator, run.people.picker]
    raw = (videos.video_dir(ctx.out_dir, vid) / script_write.FILENAME).read_text("utf-8")
    ai_voice = narration.ai_voice(store_videos.assets(conn, vid))
    post = publish.post_for(script.from_json(raw), story.collection, ai_voice)
    mid = run.relay.send(file_message(to, vid, story.title, done.render, _copy(post, ai_voice)))
    store_daily.add_step(conn, vid, "sent", mid, run.clock())
    return f"sent {done.render} (${done.cost_usd():.2f})"


AI_LABEL = (
    "The voice is AI: turn on the AI label when you upload",
    "YouTube: Altered or synthetic content, Yes. Instagram: AI info. TikTok: AI-generated content.",
)


def _copy(post: publish.Post, ai_voice: bool) -> list[tuple[str, str]]:
    """The post text to copy, after the AI-label reminder when Kokoro read it."""
    return [*([AI_LABEL] if ai_voice else []), *post.for_copying()]


def _start_voice(run: Run, vid: str, title: str, sent: dict[str, str]) -> str:
    """Kokoro reads the script, or Tara is asked for her take; "" once that is done."""
    conn = run.conn
    if run.narration == narration.KOKORO:
        if "voice" in store_videos.stage_runs(conn, vid):
            return ""
        pipeline.advance(conn, vid, None, run.context(vid))  # read-this.txt from the script
        narrate(run, vid)
        return "kokoro read the script"
    if "take" in sent:
        return ""
    pipeline.advance(conn, vid, None, run.context(vid))
    return ask_take(run, vid, title)


def unfinished(conn: sqlite3.Connection) -> list[str]:
    """One line per daily video not yet sent or cancelled: ID, day, story, step."""
    lines: list[str] = []
    for vid in store_daily.unfinished(conn):
        video = store_videos.get_video(conn, vid)
        title = _story(conn, video.story_id).title if video.story_id else "-"
        failed = " (failed)" if "failed" in store_daily.steps(conn, vid) else ""
        lines.append(f"{vid}\t{video.created_at.date()}\t{title}\t{_where(conn, vid)}{failed}")
    return lines


def _where(conn: sqlite3.Connection, vid: str) -> str:
    sent, runs = store_daily.steps(conn, vid), store_videos.stage_runs(conn, vid)
    if store_videos.get_video(conn, vid).story_id is None:
        return "story pick"
    if "script" not in runs:
        return "script"
    if "voice" not in runs and "take" not in sent:
        return "the voice"
    if "voice" in runs and "visuals" in runs:
        return "render and send"
    return "the take and pictures"


def _error(run: Run, vid: str, err: Exception) -> str:
    """Count an error in a row; the next tick tries again until there are
    MAX_ERRORS, then the video stops once the picker has been alerted."""
    count = store_daily.add_error(run.conn, vid, run.clock())
    if count < MAX_ERRORS:
        extra = {"video_id": vid, "errors": count, "detail": str(err)}
        log.warning("daily run error; the next tick tries again", extra=extra)
        return f"error {count} of {MAX_ERRORS}, trying again next tick: {err}"
    return _stop(run, vid, str(err))


def _stop(run: Run, vid: str, detail: str) -> str:
    """Stop a video once the picker has been alerted; until then the next tick
    tries again."""
    now, where = run.clock(), _where(run.conn, vid)
    log.error("daily run failed", extra={"video_id": vid, "step": where, "detail": detail})
    try:
        alert = alert_message(run.people.picker, vid, where, detail, now.isoformat())
        mid = run.relay.send(alert)
    except (RelayError, web.WebError) as alert_err:
        log.exception("alert not sent", extra={"video_id": vid, "detail": str(alert_err)})
        return f"failed, alert not sent yet (the next tick tries again): {detail}"
    store_daily.add_step(run.conn, vid, "failed", mid, now)
    store_daily.clear_errors(run.conn, vid)
    store_daily.end(run.conn, vid)
    return f"failed: {detail}"
