"""Captions stage, I/O edge: transcribe Tara's take with word times, align it.

Whisper runs on this machine, free and offline: `reel_bot.whisper` in a child
process at low priority, reading the model from `MODEL_DIR` (`make models` fills
it once). A child process gives all of Whisper's memory back before the render
starts, and can be timed out. `make` is idempotent: a recorded captions stage
with its file on disk is reused without transcribing again.
"""

import json
import logging
import math
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from reel_bot import captions, script, store_videos, videos, voice

log = logging.getLogger(__name__)

MODEL = "base.en"  # English-only Whisper, small enough for the VPS (about 430 MB while it runs)
MODEL_DIR = Path("models") / f"whisper-{MODEL}"  # relative to the checkout; see `make models`
WHISPER = [sys.executable, "-m", "reel_bot.whisper"]  # + model dir and audio file
TIMEOUT_S = 900  # a 50 s take takes about 90 s on the VPS
MIN_MATCH = 0.5  # below this the take is not a reading of this script
WARN_MATCH = 0.85
FILENAME = "captions.json"


@dataclass(frozen=True)
class Heard:
    words: tuple[captions.Word, ...]
    seconds: float  # the audio's length


# audio file -> what Whisper heard
Transcribe = Callable[[Path], Heard]


def parse(raw: bytes) -> Heard:
    """What `reel_bot.whisper` printed; anything odd raises CaptionsError."""
    try:
        data = cast(dict[str, Any], json.loads(raw))
        words = tuple(
            captions.Word(str(w).strip(), float(start), float(end))
            for w, start, end in cast(list[list[Any]], data["words"])
        )
        seconds = float(data["duration"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError) as err:
        raise captions.CaptionsError("Whisper's output has no word times") from err
    if not 0 <= seconds < math.inf or any(not 0 <= w.start <= w.end < math.inf for w in words):
        raise captions.CaptionsError("Whisper's output has impossible word times")
    return Heard(words, seconds)


def local(model_dir: Path = MODEL_DIR, timeout_s: float = TIMEOUT_S) -> Transcribe:
    """A Transcribe running Whisper on this machine, in a child process."""
    if not (model_dir / "model.bin").is_file():
        raise captions.CaptionsError(f"no Whisper model in {model_dir}: run make models")

    def transcribe(audio: Path) -> Heard:
        args = [*WHISPER, str(model_dir), str(audio)]
        if shutil.which("nice"):
            args = ["nice", "-n", "10", *args]
        try:
            done = subprocess.run(args, capture_output=True, timeout=timeout_s, check=True)  # noqa: S603
        except subprocess.CalledProcessError as err:
            last = err.stderr.decode(errors="replace").strip().splitlines()[-1:]
            raise captions.CaptionsError(
                f"Whisper exited {err.returncode}: {''.join(last)}"
            ) from err
        except subprocess.TimeoutExpired as err:
            raise captions.CaptionsError(f"Whisper took over {timeout_s:.0f} s") from err
        return parse(done.stdout)

    return transcribe


def make(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    transcribe: Transcribe,
    clock: Callable[[], datetime],
) -> captions.Captions:
    """The video's captions: from the cache, or transcribed, aligned and recorded."""
    store_videos.get_video(conn, video_id)  # an unknown video is refused here
    folder = videos.video_dir(out_dir, video_id)
    path = folder / FILENAME
    runs = store_videos.stage_runs(conn, video_id)
    if "captions" in runs and path.is_file():
        log.info("captions reused", extra={"video_id": video_id})
        return captions.from_json(path.read_text(encoding="utf-8"))
    if "voice" not in runs:
        raise captions.CaptionsError(f"video {video_id} has no voice take yet")
    take = folder / voice.FILENAME
    s = script.from_json((folder / "script.json").read_text(encoding="utf-8"))

    started = clock()
    got = transcribe(take)
    built = captions.build(video_id, s, got.words)
    extra = {"video_id": video_id, "matched": round(built.matched, 2)}
    if built.matched < MIN_MATCH:
        raise captions.CaptionsError(
            f"only {built.matched:.0%} of the script was heard in the take; "
            "is it the right recording?"
        )
    if built.matched < WARN_MATCH:
        log.warning("take differs from the script", extra=extra)
    raw = captions.to_json(built)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)
    finished = clock()
    store_videos.record_stage(
        conn,
        video_id,
        videos.StageRun("captions", FILENAME, 0.0, (finished - started).total_seconds(), finished),
    )
    log.info("captions made", extra={**extra, "cues": len(built.cues)})
    return captions.from_json(raw)  # as stored, so a rerun returns the same value
