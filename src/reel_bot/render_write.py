"""Render stage, I/O edge: run ffmpeg on the pictures, captions and take.

ffmpeg runs as an argument list (never through a shell), at low CPU priority
with two threads, inside the video's directory. The result is checked with
ffprobe before it replaces an earlier render. A finished render is reused.
"""

import json
import logging
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from reel_bot import captions, captions_write, render, store_videos, videos, visuals_write, voice

log = logging.getLogger(__name__)

FILENAME = "render.mp4"
CAPTIONS_FILE = "captions.ass"
TIMEOUT_S = 900
TOLERANCE_S = 0.5  # how far the render may differ from the take in length
FONT_SOURCE = "fonts-dejavu-core (Ubuntu package), https://dejavu-fonts.github.io"
FONT_LICENCE = "DejaVu fonts licence (Bitstream Vera derived): free, commercial use allowed"


@dataclass(frozen=True)
class Render:
    video_id: str
    path: Path
    seconds: float


def _run(args: list[str], cwd: Path, timeout: float) -> str:
    if shutil.which(args[0]) is None:
        raise render.RenderError(f"{args[0]} is not installed")
    if shutil.which("nice"):
        args = ["nice", "-n", "10", *args]
    try:
        done = subprocess.run(  # noqa: S603 (fixed binary, argument list, no shell)
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True
        )
    except subprocess.CalledProcessError as err:
        last = (err.stderr or "").strip().splitlines()[-3:]
        raise render.RenderError(f"ffmpeg exited {err.returncode}: {' | '.join(last)}") from err
    except subprocess.TimeoutExpired as err:
        raise render.RenderError(f"{args[0]} took over {timeout:.0f} s") from err
    return done.stdout


def _probe(path: Path) -> tuple[int, int, float, bool]:
    """(width, height, seconds, has audio) of a rendered file."""
    out = _run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height"
         ":format=duration", "-of", "json", path.name],
        path.parent, 60,
    )  # fmt: skip
    data = cast(dict[str, Any], json.loads(out))
    streams = cast(list[dict[str, Any]], data.get("streams") or [])
    empty: dict[str, Any] = {}
    video = next((s for s in streams if s.get("codec_type") == "video"), empty)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return (
        int(video.get("width", 0)),
        int(video.get("height", 0)),
        float(data["format"]["duration"]),
        has_audio,
    )


def _take_seconds(take: Path) -> float:
    try:
        return voice.duration(_run(voice.probe_args(Path(take.name)), take.parent, 60))
    except voice.VoiceError as err:
        raise render.RenderError(f"cannot read the take: {err}") from err


def make(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    clock: Callable[[], datetime],
) -> Render:
    """The finished 1080x1920 MP4: reused when rendered, otherwise rendered and checked."""
    store_videos.get_video(conn, video_id)  # an unknown video is refused here
    folder = videos.video_dir(out_dir, video_id)
    path = folder / FILENAME
    runs = store_videos.stage_runs(conn, video_id)
    if "render" in runs and path.is_file():
        log.info("render reused", extra={"video_id": video_id})
        return Render(video_id, path, _probe(path)[2])
    missing = [s for s in ("voice", "captions", "visuals") if s not in runs]
    if missing:
        raise render.RenderError(f"video {video_id} still needs: {', '.join(missing)}")

    started = clock()
    cap = captions.from_json((folder / captions_write.FILENAME).read_text(encoding="utf-8"))
    pictures = render.pictures_from((folder / visuals_write.FILENAME).read_text(encoding="utf-8"))
    total = _take_seconds(folder / voice.FILENAME)
    shots = render.timeline(cap, pictures, total)
    (folder / CAPTIONS_FILE).write_text(render.ass(cap.cues), encoding="utf-8")
    tmp = folder / "render.tmp.mp4"
    _run(render.ffmpeg_args(shots, voice.FILENAME, CAPTIONS_FILE, tmp.name), folder, TIMEOUT_S)
    width, height, seconds, has_audio = _probe(tmp)
    if (width, height) != (render.WIDTH, render.HEIGHT) or not has_audio:
        raise render.RenderError(f"the render is {width}x{height}, audio={has_audio}")
    if abs(seconds - total) > TOLERANCE_S:
        raise render.RenderError(f"the render is {seconds:.1f} s; the take is {total:.1f} s")
    tmp.replace(path)
    finished = clock()
    store_videos.add_asset(
        conn,
        video_id,
        videos.Asset(f"font:{render.FONT}", "font", FONT_SOURCE, FONT_LICENCE, finished),
    )
    store_videos.record_stage(
        conn,
        video_id,
        videos.StageRun("render", FILENAME, 0.0, (finished - started).total_seconds(), finished),
    )
    log.info("rendered", extra={"video_id": video_id, "seconds": round(seconds, 1)})
    return Render(video_id, path, seconds)
