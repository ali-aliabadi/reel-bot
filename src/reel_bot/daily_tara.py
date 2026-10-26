"""Tara's steps in the daily run, one message at a time: the script from ChatGPT,
the voice note, then each picture, each asked for once the one before is in.

Relay locks an answer once it is read, so an answer that can't be used (a silent
take, a file that isn't a picture) is asked for again in a new message that says
why, at most MAX_ASKS times; each ask is a `daily_steps` row, so a rerun never
asks twice.
"""

import logging
from pathlib import Path

from reel_bot import (
    guide,
    pipeline,
    script,
    script_write,
    store_daily,
    store_videos,
    videos,
    visuals,
    visuals_write,
    voice,
)
from reel_bot.daily_run import DailyError, Run, poll
from reel_bot.relay_api import Answer, again, picture_message, script_message, take_message
from reel_bot.stories import Story

log = logging.getLogger(__name__)

SCRIPT_TRIES = 3  # ChatGPT's first script and up to two fixes (store.py allows script-1..3)
MAX_ASKS = 3  # times one take or picture question is sent before the video stops


def script_step(run: Run, video: videos.Video, story: Story, sent: dict[str, str]) -> str:
    """Ask Tara for ChatGPT's script, check her answer, and ask for a fix while it
    has problems. Returns what it waits for, "" once the script is recorded."""
    conn, vid = run.conn, video.id
    tries = sum(step.startswith("script-") for step in sent)
    if tries == 0:
        paste = script.chat_prompt(story, video)
    else:
        got = poll(run, sent[f"script-{tries}"])
        if got is None:
            return "waiting for the script"
        found = script_write.add(conn, run.context(vid).out_dir, vid, got.text, run.clock)
        if not found:
            return ""
        if tries == SCRIPT_TRIES:
            raise DailyError(f"ChatGPT's script still has problems: {'; '.join(found)}")
        paste = script.chat_fix(found)
    msg = script_message(run.people.narrator, vid, story.title, paste, tries + 1)
    store_daily.add_step(conn, vid, f"script-{tries + 1}", run.relay.send(msg), run.clock())
    return "asked for the script" if tries == 0 else f"asked for fix {tries} of the script"


def _take_message(run: Run, vid: str, title: str) -> dict[str, object]:
    folder = videos.video_dir(run.context(vid).out_dir, vid)
    listen = folder / guide.FILENAME
    text = (folder / pipeline.READ_FILE).read_text("utf-8").strip()
    return take_message(run.people.narrator, vid, title, text, listen if listen.is_file() else None)


def _make_guide(run: Run, vid: str) -> Path | None:
    """Tara's reading guide; a guide that can't be made is logged and skipped, since
    Tara can read without it."""
    ctx = run.context(vid)
    if ctx.speak is None:
        return None
    folder = videos.video_dir(ctx.out_dir, vid)
    try:
        return guide.make(folder, (folder / pipeline.READ_FILE).read_text("utf-8"), ctx.speak())
    except guide.GuideError as err:
        log.warning("no reading guide", extra={"video_id": vid, "error": str(err)})
        return None


def ask_take(run: Run, vid: str, title: str) -> str:
    _make_guide(run, vid)
    mid = run.relay.send(_take_message(run, vid, title))
    store_daily.add_step(run.conn, vid, "take", mid, run.clock())
    return "asked for the take"


def _ask_again(run: Run, vid: str, step: str, message: dict[str, object], problem: str) -> None:
    asks = store_daily.asks(run.conn, vid, step)
    if asks >= MAX_ASKS:
        raise DailyError(f"{step} still can't be used after {asks} asks: {problem}")
    mid = run.relay.send(again(message, asks + 1, problem))
    store_daily.reask(run.conn, vid, step, mid, run.clock())
    log.info("asked again", extra={"video_id": vid, "step": step, "message_id": mid})


def _save_picture(run: Run, vid: str, number: int, got: Answer) -> str:
    """Save an answered picture; returns why it can't be used, "" when saved."""
    if got.image is None:
        return "the answer has no picture"
    made = visuals_write.Made(got.image, got.recipient, run.clock())
    try:
        visuals_write.add(run.conn, run.context(vid).out_dir, vid, number, made)
    except visuals.VisualsError as err:
        return str(err)
    return ""


def _save_take(run: Run, vid: str, got: Answer) -> str:
    """Import an answered take as the voice stage; returns why it can't be used."""
    if got.audio is None:
        return "the answer has no audio"
    out = run.context(vid).out_dir
    take = videos.video_dir(out, vid) / f"take{got.audio_suffix()}"
    take.write_bytes(got.audio)
    try:
        voice.import_take(run.conn, out, vid, take, run.clock)
    except voice.BadRecording as err:
        return str(err)
    return ""


def collect(run: Run, vid: str, title: str, sent: dict[str, str]) -> None:
    """Save the take and every picture that has arrived; ask again for any that
    can't be used."""
    conn, out = run.conn, run.context(vid).out_dir
    folder, pictures = videos.video_dir(out, vid), visuals_write.plan(conn, out, vid).pictures
    for n, pic in enumerate(pictures, 1):
        step = f"picture-{n}"
        if step not in sent or visuals_write.found(folder, pic) is not None:
            continue
        if (got := poll(run, sent[step])) and (problem := _save_picture(run, vid, n, got)):
            prompts = [p.prompt for p in pictures]
            msg = picture_message(run.people.narrator, vid, title, prompts, n)
            _ask_again(run, vid, step, msg, problem)
    if "voice" in store_videos.stage_runs(conn, vid) or not (got := poll(run, sent["take"])):
        return
    if problem := _save_take(run, vid, got):
        _ask_again(run, vid, "take", _take_message(run, vid, title), problem)


def next_picture(run: Run, vid: str, title: str, sent: dict[str, str]) -> int:
    """Ask Tara for the next picture once her take and every earlier picture are
    in, so she gets one step at a time. Returns its number, 0 when none was asked."""
    conn, out = run.conn, run.context(vid).out_dir
    if "voice" not in store_videos.stage_runs(conn, vid):
        return 0
    folder, pictures = videos.video_dir(out, vid), visuals_write.plan(conn, out, vid).pictures
    for n, pic in enumerate(pictures, 1):
        if f"picture-{n}" not in sent:
            msg = picture_message(run.people.narrator, vid, title, [p.prompt for p in pictures], n)
            store_daily.add_step(conn, vid, f"picture-{n}", run.relay.send(msg), run.clock())
            return n
        if visuals_write.found(folder, pic) is None:
            return 0
    return 0
