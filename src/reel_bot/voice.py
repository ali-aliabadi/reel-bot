"""Voice stage: import Tara's recording, clean it and normalize its loudness.

The core builds the ffmpeg arguments and judges the result; `import_take`
runs ffmpeg/ffprobe (as argument lists, never through a shell) and records the
stage. Tara's audio is never logged, only its length.
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

from reel_bot import store_videos, videos

log = logging.getLogger(__name__)

FILENAME = "voice.wav"
MAX_INPUT_BYTES = 100 << 20
TIMEOUT_S = 120
# Spoken length each length setting expects; outside it the take still imports,
# with a warning, because Tara's reading is the human part and is not redone by a rule.
SECONDS = {"short": (35.0, 60.0), "long": (55.0, 100.0)}
_TRIM = "silenceremove=start_periods=1:start_silence=0.15:start_threshold=-50dB"
FILTERS = ",".join(
    (
        "highpass=f=80",  # rumble and handling noise
        "afftdn=nf=-25",  # steady background hiss
        _TRIM,  # silence before the first word
        "areverse",
        _TRIM,  # silence after the last word
        "areverse",
        "loudnorm=I=-14:TP=-1.5:LRA=11",  # YouTube's playback loudness
    )
)


class VoiceError(Exception):
    pass


@dataclass(frozen=True)
class Voice:
    video_id: str
    path: Path
    seconds: float


def clean_args(src: Path, dst: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-af", FILTERS, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst),
    ]  # fmt: skip


def probe_args(path: Path) -> list[str]:
    return [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path),
    ]  # fmt: skip


def duration(probe_json: str) -> float:
    """Seconds of audio from ffprobe's JSON; raises VoiceError when there is none."""
    try:
        data = json.loads(probe_json)
        if not data.get("streams"):
            raise VoiceError("the file has no audio")
        return float(data["format"]["duration"])
    except (ValueError, KeyError, TypeError) as err:
        raise VoiceError("ffprobe could not read the file") from err


def length_warning(seconds: float, length: str) -> str:
    low, high = SECONDS[length]
    if low <= seconds <= high:
        return ""
    return f"the take is {seconds:.0f} s; a {length} video expects {low:.0f}-{high:.0f} s"


def _run(args: list[str]) -> str:
    if shutil.which(args[0]) is None:
        raise VoiceError(f"{args[0]} is not installed")
    try:
        done = subprocess.run(  # noqa: S603 (fixed binary, argument list, no shell)
            args, capture_output=True, text=True, timeout=TIMEOUT_S, check=True
        )
    except subprocess.CalledProcessError as err:
        raise VoiceError(f"{args[0]} exited {err.returncode}") from err
    except subprocess.TimeoutExpired as err:
        raise VoiceError(f"{args[0]} took over {TIMEOUT_S} s") from err
    return done.stdout


def import_take(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    take: Path,
    clock: Callable[[], datetime],
) -> Voice:
    """Clean a recording into out/<video>/voice.wav and record the stage. A new
    take replaces the old one. The video must already have its script."""
    video = store_videos.get_video(conn, video_id)
    if "script" not in store_videos.stage_runs(conn, video_id):
        raise VoiceError(f"video {video_id} has no script yet, so there is nothing to read")
    if not take.is_file():
        raise VoiceError(f"no recording at {take}")
    if take.stat().st_size > MAX_INPUT_BYTES:
        raise VoiceError(f"the recording is over {MAX_INPUT_BYTES >> 20} MB")
    duration(_run(probe_args(take)))  # refuse a file with no audio before cleaning

    started = clock()
    folder = videos.video_dir(out_dir, video_id)
    tmp, dst = folder / "voice.tmp.wav", folder / FILENAME
    _run(clean_args(take, tmp))
    seconds = duration(_run(probe_args(tmp)))
    tmp.replace(dst)
    if warning := length_warning(seconds, video.length):
        log.warning("voice length", extra={"video_id": video_id, "detail": warning})
    finished = clock()
    store_videos.record_stage(
        conn,
        video_id,
        videos.StageRun("voice", FILENAME, 0.0, (finished - started).total_seconds(), finished),
    )
    log.info("voice imported", extra={"video_id": video_id, "seconds": round(seconds, 1)})
    return Voice(video_id, dst, seconds)
