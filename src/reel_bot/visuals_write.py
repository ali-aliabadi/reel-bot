"""Visuals stage, I/O edge: the pictures Tara makes from each paragraph's prompt.

Tara makes each picture in ChatGPT from its prompt (`visuals.plan`) and sends it
back through Relay; ali can also add one by hand (`reel-bot visuals --add`).
`add` saves a picture as it arrives, keeping its own type, and records it as an
asset with its source and licence. `finish` records the stage once every
picture is in. Pictures cost reel-bot nothing.
"""

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from reel_bot import script, store_videos, videos, visuals

log = logging.getLogger(__name__)

LICENCE = "OpenAI terms of use: output assigned to the user, commercial use allowed"
FILENAME = "visuals.json"
LATER = ("visuals", "render")  # stages a new picture makes stale


@dataclass(frozen=True)
class Made:
    """A picture as it arrived: its bytes, who made it in ChatGPT, and when."""

    data: bytes
    by: str
    at: datetime


def plan(conn: sqlite3.Connection, out_dir: Path, video_id: str) -> visuals.Visuals:
    """The pictures the video's script needs, with their prompts."""
    store_videos.get_video(conn, video_id)  # an unknown video is refused here
    if "script" not in store_videos.stage_runs(conn, video_id):
        raise visuals.VisualsError(f"video {video_id} has no script yet")
    path = videos.video_dir(out_dir, video_id) / "script.json"
    return visuals.plan(script.from_json(path.read_text(encoding="utf-8")))


def found(folder: Path, pic: visuals.Picture) -> Path | None:
    """The picture's file, whichever type it is, or None while it is missing."""
    paths = (folder / f"{pic.name}{s}" for s in visuals.SUFFIXES)
    return next((p for p in paths if p.is_file()), None)


def add(conn: sqlite3.Connection, out_dir: Path, video_id: str, number: int, made: Made) -> Path:
    """Save picture `number` (from 1); replaces an earlier one."""
    pictures = plan(conn, out_dir, video_id).pictures
    if not 1 <= number <= len(pictures):
        raise visuals.VisualsError(f"video {video_id} has pictures 1 to {len(pictures)}")
    pic, suffix = pictures[number - 1], visuals.suffix(made.data)
    folder = videos.video_dir(out_dir, video_id)
    if (old := found(folder, pic)) is not None:
        old.unlink()
        store_videos.clear_stages(conn, video_id, LATER)
    path = folder / f"{pic.name}{suffix}"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(made.data)
    tmp.replace(path)
    asset = videos.Asset(path.name, "image", f"made in ChatGPT by {made.by}", LICENCE, made.at)
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
