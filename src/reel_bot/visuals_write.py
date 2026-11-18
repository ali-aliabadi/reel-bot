"""Visuals stage, I/O edge: the pictures Tara makes from each paragraph's prompt.

Tara makes each picture in ChatGPT from its prompt (`visuals.plan`), or picks
reel-bot's draft of it (`drafts`), through Relay; ali can also add one by hand
(`reel-bot visuals --add`). `add` saves a picture as it arrives, keeping its own
type, and records it as an asset with its source and licence. `finish` records
the stage once every picture is in. Pictures cost reel-bot nothing (drafts come
from Workers AI's free allocation).
"""

import json
import logging
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from reel_bot import script, store_videos, videos, visuals

log = logging.getLogger(__name__)

LICENCE = "OpenAI terms of use: output assigned to the user, commercial use allowed"
GEMINI_LICENCE = "Google AI Studio terms: Google does not claim ownership of original output"
ANIMATION_TIMEOUT_S = 30
FILENAME = "visuals.json"
LATER = ("visuals", "render")  # stages a new picture makes stale


@dataclass(frozen=True)
class Made:
    """A picture as it arrived: its bytes, who made it (or chose reel-bot's draft),
    and when; provenance defaults to ChatGPT for stills and Gemini for animations."""

    data: bytes
    by: str
    at: datetime
    source: str = ""
    licence: str = ""


def plan(conn: sqlite3.Connection, out_dir: Path, video_id: str) -> visuals.Visuals:
    """The pictures the video's script needs, with their prompts."""
    store_videos.get_video(conn, video_id)  # an unknown video is refused here
    if "script" not in store_videos.stage_runs(conn, video_id):
        raise visuals.VisualsError(f"video {video_id} has no script yet")
    path = videos.video_dir(out_dir, video_id) / "script.json"
    return visuals.plan(script.from_json(path.read_text(encoding="utf-8")))


def found(folder: Path, pic: visuals.Picture) -> Path | None:
    """The picture's file, whichever type it is, or None while it is missing."""
    paths = (folder / f"{pic.name}{s}" for s in visuals.SCENE_SUFFIXES)
    return next((p for p in paths if p.is_file()), None)


def _check_animation(path: Path) -> None:
    """Decode one pass before saving and require an actual video stream."""
    kind = path.suffix[1:].upper()
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-abort_on", "empty_output",
        *visuals.ANIMATION_INPUTS[path.suffix], "-threads", "1",
        "-max_pixels", str(visuals.ANIMATION_MAX_PIXELS), "-i", str(path),
        "-map", "0:V:0", "-f", "null", "-",
    ]  # fmt: skip
    try:
        subprocess.run(  # noqa: S603
            args, capture_output=True, timeout=ANIMATION_TIMEOUT_S, check=True
        )
    except FileNotFoundError as err:
        raise visuals.VisualsError(f"ffmpeg is needed to import a {kind}") from err
    except subprocess.TimeoutExpired as err:
        raise visuals.VisualsError(
            f"the {kind} took over {ANIMATION_TIMEOUT_S} s to decode"
        ) from err
    except subprocess.CalledProcessError as err:
        raise visuals.VisualsError(
            f"the {kind} is unreadable or its frame size is too large"
        ) from err


def add(conn: sqlite3.Connection, out_dir: Path, video_id: str, number: int, made: Made) -> Path:
    """Save picture `number` (from 1); replaces an earlier one."""
    pictures = plan(conn, out_dir, video_id).pictures
    if not 1 <= number <= len(pictures):
        raise visuals.VisualsError(f"video {video_id} has pictures 1 to {len(pictures)}")
    pic, suffix = pictures[number - 1], visuals.scene_suffix(made.data)
    animated = suffix in visuals.ANIMATION_INPUTS
    folder = videos.video_dir(out_dir, video_id)
    old = found(folder, pic)
    path = folder / f"{pic.name}{suffix}"
    with tempfile.TemporaryDirectory(dir=folder) as staging:
        tmp = Path(staging) / path.name
        tmp.write_bytes(made.data)
        if animated:
            _check_animation(tmp)
        if old is not None:
            store_videos.clear_stages(conn, video_id, LATER)
        tmp.replace(path)
    if old is not None and old != path:
        old.unlink()
    provider = "Gemini" if animated else "ChatGPT"
    source = made.source or f"made in {provider} by {made.by}"
    licence = made.licence or (GEMINI_LICENCE if animated else LICENCE)
    asset = videos.Asset(path.name, "footage" if animated else "image", source, licence, made.at)
    store_videos.add_asset(conn, video_id, asset)
    log.info("picture added", extra={"video_id": video_id, "section": pic.section})
    return path


def finish(
    conn: sqlite3.Connection, out_dir: Path, video_id: str, clock: Callable[[], datetime]
) -> list[int]:
    """The numbers of the pictures still missing; once there are none, the stage is
    recorded (the render reads the files from its manifest)."""
    pictures = plan(conn, out_dir, video_id).pictures
    folder = videos.video_dir(out_dir, video_id)
    paths = [found(folder, p) for p in pictures]
    missing = [n for n, path in enumerate(paths, 1) if path is None]
    if missing or "visuals" in store_videos.stage_runs(conn, video_id):
        return missing
    files = [path.name for path in paths if path is not None]  # all of them, here
    manifest = {
        "video_id": video_id,
        "pictures": [
            {"section": p.section, "path": f, "prompt": p.prompt}
            for p, f in zip(pictures, files, strict=True)
        ],
    }
    (folder / FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    store_videos.record_stage(
        conn, video_id, videos.StageRun("visuals", FILENAME, 0.0, 0.0, clock())
    )
    log.info("visuals made", extra={"video_id": video_id, "pictures": len(pictures)})
    return []
