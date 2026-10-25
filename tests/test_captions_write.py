import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import captions, captions_write, script, store, store_videos, videos
from reel_bot.captions import Word
from reel_bot.captions_write import Heard
from reel_bot.script import Paragraph, Script
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=2)
        return self.t


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [FOX])
    return c


def voiced(conn: sqlite3.Connection, out: Path, with_voice: bool = True) -> tuple[Video, Script]:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    store_videos.choose_story(conn, v.id, FOX.id, "ali", NOW)
    s = Script(
        v.id, FOX.id, FOX.title, "A fox wanted grapes.",
        (Paragraph("He jumped and missed.", "A fox."), Paragraph("He left, proud.", "A fox.")),
        "", FOX.source, FOX.source_ref,
    )  # fmt: skip
    folder = videos.video_dir(out, v.id)
    (folder / "script.json").write_text(script.to_json(s), encoding="utf-8")
    store_videos.record_stage(conn, v.id, StageRun("script", "script.json", 0.01, 1, NOW))
    if with_voice:
        (folder / "voice.wav").write_bytes(b"RIFF")
        store_videos.record_stage(conn, v.id, StageRun("voice", "voice.wav", 0, 1, NOW))
    return v, s


class Fake:
    def __init__(self, words: list[str]) -> None:
        self.words = words

    def __call__(self, audio: Path) -> Heard:
        assert audio.name == "voice.wav"
        return Heard(tuple(Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(self.words)), 60)


def test_makes_caches_and_records_the_stage(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v, s = voiced(conn, tmp_path / "out")
    fake = Fake([w.strip(".,").lower() for w in s.spoken().split()])
    c = captions_write.make(conn, tmp_path / "out", v.id, fake, Clock())
    assert c.matched == 1.0
    run = store_videos.stage_runs(conn, v.id)["captions"]
    assert (run.cost_usd, run.seconds) == (0.0, 2)
    assert captions_write.make(conn, tmp_path / "out", v.id, Fake([]), Clock()) == c


def test_refuses_a_take_that_is_not_this_script(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v, _ = voiced(conn, tmp_path / "out")
    with pytest.raises(captions.CaptionsError, match="right recording"):
        captions_write.make(conn, tmp_path / "out", v.id, Fake(["hello", "there"]), Clock())
    assert "captions" not in store_videos.stage_runs(conn, v.id)


def test_needs_the_voice_stage(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v, _ = voiced(conn, tmp_path / "out", with_voice=False)
    with pytest.raises(captions.CaptionsError, match="no voice take"):
        captions_write.make(conn, tmp_path / "out", v.id, Fake([]), Clock())


def test_parse_reads_whisper_output() -> None:
    raw = b'{"duration": 3, "words": [[" Fox", 0.1, 0.4]]}'
    assert captions_write.parse(raw) == Heard((Word("Fox", 0.1, 0.4),), 3.0)
    with pytest.raises(captions.CaptionsError):
        captions_write.parse(b'{"text": "hi"}')


def test_local_needs_the_model(tmp_path: Path) -> None:
    with pytest.raises(captions.CaptionsError, match="make models"):
        captions_write.local(tmp_path / "no-model")


def fake_whisper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: str) -> Path:
    """Stand in for `reel_bot.whisper` with a Python one-liner; returns a model dir."""
    monkeypatch.setattr(captions_write, "WHISPER", [sys.executable, "-c", code])
    (tmp_path / "model.bin").write_bytes(b"")
    return tmp_path


def test_local_reads_the_child_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code = 'import sys; print(\'{"duration": 2, "words": [[" Fox", 0.1, 0.4]]}\')'
    model = fake_whisper(monkeypatch, tmp_path, code)
    assert captions_write.local(model)(tmp_path / "voice.wav") == Heard((Word("Fox", 0.1, 0.4),), 2)


def test_local_reports_a_failed_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model = fake_whisper(monkeypatch, tmp_path, "import sys; sys.exit('no audio stream')")
    with pytest.raises(captions.CaptionsError, match="exited 1: no audio stream"):
        captions_write.local(model)(tmp_path / "voice.wav")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not (captions_write.MODEL_DIR / "model.bin").is_file(),
    reason="needs ffmpeg and the Whisper model (make models)",
)
def test_local_transcribes_a_wav(tmp_path: Path) -> None:
    audio = tmp_path / "voice.wav"
    silence = ["-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "2", str(audio)]
    subprocess.run(["ffmpeg", "-loglevel", "error", *silence], check=True)  # noqa: S603, S607
    got = captions_write.local()(audio)
    assert got.seconds == pytest.approx(2.0, abs=0.1)
    assert all(0 <= w.start <= w.end <= got.seconds + 0.5 for w in got.words)
