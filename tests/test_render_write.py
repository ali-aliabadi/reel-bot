import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import captions, render, render_write, store, store_videos, videos
from reel_bot.captions import Captions, Cue, Section
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; the render smoke test needs them",
)


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [FOX])
    return c


def new_video(conn: sqlite3.Connection) -> Video:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    return v


def test_refuses_until_the_earlier_stages_are_done(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    v = new_video(conn)
    store_videos.record_stage(conn, v.id, StageRun("voice", "voice.wav", 0, 1, NOW))
    with pytest.raises(render.RenderError, match="still needs: captions, visuals"):
        render_write.make(conn, tmp_path / "out", v.id, Clock())


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # noqa: S603, S607


@needs_ffmpeg
def test_renders_a_five_second_clip(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "out"
    v = new_video(conn)
    folder = videos.video_dir(out, v.id)
    for i, color in ((1, "navy"), (2, "goldenrod")):
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=1024x1536", "-frames:v", "1",
                str(folder / f"picture-{i}.png"))  # fmt: skip
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=5", "-ar", "48000", "-ac", "1",
            str(folder / "voice.wav"))  # fmt: skip
    cap = Captions(
        v.id,
        (),
        (Cue("A fox", 0.2, 1.0), Cue("wanted grapes", 1.0, 2.0), Cue("He left", 3.0, 4.5)),
        (Section("hook", 0.2, 2.0), Section("p1", 2.2, 2.8), Section("p2", 3.0, 4.5)),
        1.0,
    )
    (folder / "captions.json").write_text(captions.to_json(cap), encoding="utf-8")
    pictures = [{"section": f"p{i}", "path": f"picture-{i}.png", "prompt": "x"} for i in (1, 2)]
    (folder / "visuals.json").write_text(json.dumps({"pictures": pictures}), encoding="utf-8")
    for stage, path in (("voice", "voice.wav"), ("captions", "captions.json"),
                        ("visuals", "visuals.json")):  # fmt: skip
        store_videos.record_stage(conn, v.id, StageRun(stage, path, 0, 1, NOW))

    r = render_write.make(conn, out, v.id, Clock())
    assert r.path == folder / "render.mp4"
    assert r.seconds == pytest.approx(5.0, abs=0.2)
    width, height, seconds, has_audio = render_write._probe(r.path)  # pyright: ignore[reportPrivateUsage]
    assert (width, height, has_audio) == (1080, 1920, True)
    assert seconds == pytest.approx(5.0, abs=0.2)
    assert "Dialogue:" in (folder / "captions.ass").read_text(encoding="utf-8")
    assert [a.kind for a in store_videos.assets(conn, v.id)] == ["font"]
    assert render_write.make(conn, out, v.id, Clock()).path == r.path
