"""Captions stage, I/O edge: transcribe Tara's take with word times, align it.

OpenAI's transcription API gives the word times (so captions need no model on
the machine and can later run on the VPS). The call uses the stdlib only: one
multipart POST through `openai_api`. `make` is idempotent: a
recorded captions stage with its file on disk is reused without another call.
"""

import json
import logging
import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from reel_bot import captions, openai_api, script, store_videos, videos, voice

log = logging.getLogger(__name__)

MODEL = "whisper-1"  # the transcription model that returns word timestamps
PRICE_PER_MINUTE = 0.006  # US dollars
MAX_UPLOAD_BYTES = 25 << 20  # the API's file limit
TIMEOUT_S = 120.0
PROMPT_WORDS = 150  # the API reads only the prompt's last ~224 tokens
MIN_MATCH = 0.5  # below this the take is not a reading of this script
WARN_MATCH = 0.85
FILENAME = "captions.json"


@dataclass(frozen=True)
class Heard:
    words: tuple[captions.Word, ...]
    seconds: float  # audio length the API billed

    def cost_usd(self) -> float:
        return self.seconds / 60 * PRICE_PER_MINUTE


# (audio file, prompt with the expected text) -> what the API heard
Transcribe = Callable[[Path, str], Heard]


def parse(raw: bytes) -> Heard:
    try:
        data = cast(dict[str, Any], json.loads(raw))
        words = tuple(
            captions.Word(str(w["word"]).strip(), float(w["start"]), float(w["end"]))
            for w in cast(list[dict[str, Any]], data.get("words") or [])
        )
        seconds = float(data["duration"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError) as err:
        raise captions.CaptionsError("the transcription reply has no word times") from err
    if not 0 <= seconds < math.inf or any(not 0 <= w.start <= w.end < math.inf for w in words):
        raise captions.CaptionsError("the transcription reply has impossible word times")
    return Heard(words, seconds)


def openai(
    api_key: str,
    base_url: str = openai_api.API_URL,
    sleep: Callable[[float], None] = time.sleep,
) -> Transcribe:
    """A Transcribe backed by OpenAI. A plain http base URL is allowed only for a
    local test server."""
    try:
        client = openai_api.Client(api_key, base_url, TIMEOUT_S, sleep)
    except openai_api.OpenAIError as err:
        raise captions.CaptionsError(str(err)) from err

    def transcribe(audio: Path, prompt: str) -> Heard:
        fields = [
            ("model", MODEL),
            ("language", "en"),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
            ("prompt", prompt),
        ]
        body, ctype = openai_api.form(fields, "voice.wav", "audio/wav", audio.read_bytes())
        try:
            return parse(client.post("/audio/transcriptions", body, ctype, "transcription"))
        except openai_api.OpenAIError as err:
            raise captions.CaptionsError(str(err)) from err

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
    if take.stat().st_size > MAX_UPLOAD_BYTES:
        raise captions.CaptionsError(f"the take is over {MAX_UPLOAD_BYTES >> 20} MB")
    s = script.from_json((folder / "script.json").read_text(encoding="utf-8"))

    started = clock()
    heard = transcribe(take, " ".join(s.spoken().split()[:PROMPT_WORDS]))
    built = captions.build(video_id, s, heard.words)
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
    cost = round(heard.cost_usd(), 4)
    store_videos.record_stage(
        conn,
        video_id,
        videos.StageRun("captions", FILENAME, cost, (finished - started).total_seconds(), finished),
    )
    log.info("captions made", extra={**extra, "cues": len(built.cues), "cost_usd": cost})
    return captions.from_json(raw)  # as stored, so a rerun returns the same value
