"""Videos: IDs, the per-video experiment settings and the on-disk cache.

A video is created before its story is chosen: the morning run makes one, asks
ali to pick from its story options, and every stage writes under
`<out dir>/<video id>/`. Only `store` writes the rows.
"""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ID = re.compile(r"^vid_[0-9A-HJKMNP-TV-Z]{26}$")
FRAMINGS = ("fable", "lesson")  # the fable alone, or the fable plus a short work lesson
LENGTHS = ("short", "long")  # about 45 s, or 61 to 90 s
STAGES = ("script", "voice", "captions", "visuals", "render")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class VideoError(Exception):
    pass


@dataclass(frozen=True)
class Video:
    id: str
    created_at: datetime
    framing: str
    length: str
    story_options: tuple[str, ...] = ()
    story_id: str | None = None
    chosen_by: str | None = None  # who picked the story: the human pick
    chosen_at: datetime | None = None


@dataclass(frozen=True)
class StageRun:
    """One stage's cached output for a video."""

    stage: str
    path: str  # relative to the video's directory
    cost_usd: float
    seconds: float
    finished_at: datetime


def new_id(now: datetime, rand: Callable[[int], bytes] = secrets.token_bytes) -> str:
    """A `vid_` + ULID: 48 bits of milliseconds then 80 random bits, sortable by time."""
    n = (int(now.timestamp() * 1000) << 80) | int.from_bytes(rand(10))
    chars = [_CROCKFORD[(n >> shift) & 31] for shift in range(125, -1, -5)]
    return "vid_" + "".join(chars)


def check_settings(framing: str, length: str) -> None:
    if framing not in FRAMINGS:
        raise VideoError(f"framing must be one of {', '.join(FRAMINGS)}")
    if length not in LENGTHS:
        raise VideoError(f"length must be one of {', '.join(LENGTHS)}")


def video_dir(out_dir: Path, video_id: str) -> Path:
    """The video's cache directory, created if needed. The ID is checked so a
    path can never be steered outside out_dir."""
    if not ID.match(video_id):
        raise VideoError(f"not a video ID: {video_id!r}")
    path = out_dir / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path
