import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reel_bot import pipeline, store, store_videos, videos
from reel_bot.captions import Word
from reel_bot.captions_write import Heard
from reel_bot.script_write import Reply
from reel_bot.stories import Story
from reel_bot.videos import Video
from reel_bot.visuals_write import Drawn

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
SCRIPT = json.dumps(
    {
        "hook": "A hungry fox found grapes he could not reach.",
        "paragraphs": [
            {"text": " ".join([f"word{i}"] * 25), "picture": f"A fox, scene {i}."} for i in range(4)
        ],
        "lesson": "",
    }
)
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; the end-to-end pipeline test needs them",
)


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


class Calls:
    """Fake paid clients that count their calls; `png` is what draw returns."""

    def __init__(self, png: bytes) -> None:
        self.png = png
        self.count: dict[str, int] = {"claude": 0, "transcribe": 0, "draw": 0}

    def claude(self, system: str, user: str, schema: dict[str, Any]) -> Reply:
        self.count["claude"] += 1
        return Reply(SCRIPT, "claude-opus-5-5", 1000, 1000)

    def draw(self, prompt: str) -> Drawn:
        self.count["draw"] += 1
        return Drawn(self.png, 0.05)

    def transcribe(self, audio: Path, prompt: str) -> Heard:
        self.count["transcribe"] += 1
        said = prompt.split()
        step = 5.0 / len(said)
        return Heard(tuple(Word(w, i * step, (i + 1) * step) for i, w in enumerate(said)), 6.0)

    def context(self, out: Path) -> pipeline.Context:
        return pipeline.Context(
            out, Clock(), lambda: self.claude, lambda: self.transcribe, lambda: self.draw
        )


def unreachable() -> Any:
    raise AssertionError("a cached stage built its client")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [FOX])
    return c


def new_video(conn: sqlite3.Connection, picked: bool = True) -> Video:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    if picked:
        store_videos.choose_story(conn, v.id, FOX.id, "ali", NOW)
    return v


def test_waits_for_the_pick_then_for_tara(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "out"
    calls = Calls(b"\x89PNG\r\n\x1a\nfake")
    bare = new_video(conn, picked=False)
    p = pipeline.advance(conn, bare.id, None, calls.context(out))
    assert p.waiting.startswith("a story pick")
    assert p.runs == ()

    v = new_video(conn)
    p = pipeline.advance(conn, v.id, None, calls.context(out))
    assert [r.stage for r in p.runs] == ["script", "visuals"]
    assert p.waiting.startswith("Tara's take of")
    assert p.render is None
    assert p.cost_usd() == pytest.approx(0.024 + 0.2)
    read = (out / v.id / pipeline.READ_FILE).read_text(encoding="utf-8")
    assert read.startswith("A hungry fox found grapes")
    assert calls.count == {"claude": 1, "transcribe": 0, "draw": 4}

    cached = pipeline.Context(out, Clock(), unreachable, unreachable, unreachable)
    assert pipeline.advance(conn, v.id, None, cached).runs == p.runs


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # noqa: S603, S607


@needs_ffmpeg
def test_runs_to_a_finished_render_and_redoes_it_for_a_new_take(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    png, take = tmp_path / "p.png", tmp_path / "take.wav"
    _ffmpeg("-f", "lavfi", "-i", "color=c=teal:s=1024x1536", "-frames:v", "1", str(png))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=200:duration=6", str(take))
    out = tmp_path / "out"
    calls = Calls(png.read_bytes())
    v = new_video(conn)

    p = pipeline.advance(conn, v.id, take, calls.context(out))
    assert p.waiting == ""
    assert p.render == out / v.id / "render.mp4"
    assert [r.stage for r in p.runs] == ["script", "voice", "captions", "visuals", "render"]

    cached = pipeline.Context(out, Clock(), unreachable, unreachable, unreachable)
    assert pipeline.advance(conn, v.id, None, cached).render == p.render

    again = pipeline.advance(conn, v.id, take, calls.context(out))
    assert again.render == p.render
    assert calls.count == {"claude": 1, "transcribe": 2, "draw": 4}
