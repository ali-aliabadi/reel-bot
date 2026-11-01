"""Narration: Kokoro reads the script as the video's voice, in place of a person's
take (`REEL_BOT_NARRATION=kokoro`, the default; `tara` asks Tara for a voice note).

Kokoro runs on this machine through `guide.local` (same model, voice and pace as
the reading guide), so every video has the same voice. Its reading goes through
the voice stage like a take (cleaned, trimmed, -14 LUFS) and is recorded as an
`ai-voice` asset, which puts the AI-voice disclosure in the post. `make` is
idempotent: a reading on disk is reused and Kokoro is not run again.
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from reel_bot import guide, pipeline, store_videos, videos, voice

log = logging.getLogger(__name__)

KOKORO, TARA = "kokoro", "tara"  # config.NARRATIONS
FILENAME = "narration.wav"  # Kokoro's raw reading; the voice stage makes voice.wav from it
KIND = "ai-voice"
SOURCE = f"Kokoro-82M, voice {guide.VOICE}, read on reel-bot's machine"
LICENCE = "Apache-2.0 (Kokoro-82M model and voices)"


def make(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    speak: guide.Speak,
    clock: Callable[[], datetime],
) -> voice.Voice:
    """Kokoro's reading of the video's read-this text, imported as its voice."""
    folder = videos.video_dir(out_dir, video_id)
    raw = folder / FILENAME
    if not raw.is_file():
        text = (folder / pipeline.READ_FILE).read_text("utf-8")
        tmp = folder / "narration.tmp.wav"
        speak(text, tmp)
        tmp.replace(raw)
        log.info("kokoro read the script", extra={"video_id": video_id})
    done = voice.import_take(conn, out_dir, video_id, raw, clock)
    asset = videos.Asset(voice.FILENAME, KIND, SOURCE, LICENCE, clock())
    store_videos.add_asset(conn, video_id, asset)
    return done


def ai_voice(assets: list[videos.Asset]) -> bool:
    """True when the video's voice was made by Kokoro rather than read by a person."""
    return any(a.kind == KIND for a in assets)
