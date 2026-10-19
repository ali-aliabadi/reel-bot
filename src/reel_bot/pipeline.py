"""Run every stage a video is ready for, in order, and stop where a person is needed.

A video waits for two people: ali's story pick (`reel-bot video choose`) and
Tara's take. `advance` runs script and visuals as soon as the story is picked,
then stops and says it is waiting for the take; given a take, it imports it and
runs captions and render. Every stage reuses its cached output, so running it
again costs nothing for the stages already done. Model clients are built only
when their stage actually runs, so a cached stage needs no API key.
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from reel_bot import (
    budget,
    captions,
    captions_write,
    render,
    render_write,
    script,
    script_write,
    store_videos,
    videos,
    visuals,
    visuals_write,
    voice,
)

log = logging.getLogger(__name__)

READ_FILE = "read-this.txt"  # the script as Tara reads it
ERRORS = (
    budget.BudgetError,
    script.ScriptError,
    visuals.VisualsError,
    voice.VoiceError,
    captions.CaptionsError,
    render.RenderError,
)
# Stages made from the take; a new take makes them stale.
FROM_TAKE = ("captions", "render")


@dataclass(frozen=True)
class Context:
    """Where outputs go, the clock, and factories for the paid clients (each called
    only when its stage actually runs)."""

    out_dir: Path
    clock: Callable[[], datetime]
    claude: Callable[[], script_write.Generate]
    transcribe: Callable[[], captions_write.Transcribe]
    draw: Callable[[], visuals_write.Draw]


@dataclass(frozen=True)
class Progress:
    video_id: str
    runs: tuple[videos.StageRun, ...]  # every finished stage, in pipeline order
    waiting: str  # what a person must do next; "" when the render is done
    render: Path | None

    def cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.runs), 4)

    def seconds(self) -> float:
        return round(sum(r.seconds for r in self.runs), 1)


def _progress(conn: sqlite3.Connection, video_id: str, waiting: str, out: Path | None) -> Progress:
    runs = store_videos.stage_runs(conn, video_id)
    ordered = tuple(runs[s] for s in videos.STAGES if s in runs)
    return Progress(video_id, ordered, waiting, out)


def advance(conn: sqlite3.Connection, video_id: str, take: Path | None, ctx: Context) -> Progress:
    out_dir, clock = ctx.out_dir, ctx.clock
    video = store_videos.get_video(conn, video_id)
    if video.story_id is None:
        return _progress(
            conn, video_id, f"a story pick: reel-bot video choose {video_id} <story> --by ali", None
        )
    s = script_write.write(conn, out_dir, video_id, _on_first_call(ctx.claude), clock)
    folder = videos.video_dir(out_dir, video_id)
    (folder / READ_FILE).write_text(s.spoken() + "\n", encoding="utf-8")
    visuals_write.make(conn, out_dir, video_id, _on_first_call(ctx.draw), clock)

    if take is not None:
        voice.import_take(conn, out_dir, video_id, take, clock)
        store_videos.clear_stages(conn, video_id, FROM_TAKE)
        log.info("new take; captions and render will be redone", extra={"video_id": video_id})
    elif "voice" not in store_videos.stage_runs(conn, video_id):
        return _progress(conn, video_id, f"Tara's take of {folder / READ_FILE}", None)

    captions_write.make(conn, out_dir, video_id, _on_first_call(ctx.transcribe), clock)
    done = render_write.make(conn, out_dir, video_id, clock)
    return _progress(conn, video_id, "", done.path)


def _on_first_call[**P, R](factory: Callable[[], Callable[P, R]]) -> Callable[P, R]:
    """Build the client on its first call, so a cached stage needs no API key."""
    made: list[Callable[P, R]] = []

    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        if not made:
            made.append(factory())
        return made[0](*args, **kwargs)

    return call
