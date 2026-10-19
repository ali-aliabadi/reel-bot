import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import store, store_videos, videos, voice
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; the voice import test needs them",
)


def test_clean_and_level_args_are_lists_with_the_filters(tmp_path: Path) -> None:
    clean = voice.clean_args(tmp_path / "in.ogg", tmp_path / "trim.wav")
    assert clean[0] == "ffmpeg"
    assert clean[clean.index("-af") + 1] == voice.CLEAN
    assert clean[-1] == str(tmp_path / "trim.wav")
    level = voice.level_args(tmp_path / "trim.wav", tmp_path / "out.wav")
    assert level[level.index("-af") + 1] == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert level[level.index("-ar") + 1] == "48000"


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        (json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": "47.5"}}), True),
        (json.dumps({"streams": [], "format": {"duration": "3"}}), False),
        ("not json", False),
    ],
)
def test_duration(raw: str, ok: bool) -> None:
    if ok:
        assert voice.duration(raw) == 47.5
    else:
        with pytest.raises(voice.VoiceError):
            voice.duration(raw)


def test_length_warning() -> None:
    assert voice.length_warning(45, "short") == ""
    assert "expects 35-60 s" in voice.length_warning(80, "short")
    assert voice.length_warning(80, "long") == ""


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [FOX])
    return c


def scripted(conn: sqlite3.Connection, with_script: bool = True) -> Video:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    store_videos.choose_story(conn, v.id, FOX.id, "ali", NOW)
    if with_script:
        store_videos.record_stage(conn, v.id, StageRun("script", "script.json", 0.02, 4, NOW))
    return v


def clock() -> datetime:
    return NOW + timedelta(seconds=2)


def test_refuses_before_the_script(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = scripted(conn, with_script=False)
    with pytest.raises(voice.VoiceError, match="no script"):
        voice.import_take(conn, tmp_path / "out", v.id, tmp_path / "take.ogg", clock)


def test_refuses_a_missing_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = scripted(conn)
    with pytest.raises(voice.VoiceError, match="no recording"):
        voice.import_take(conn, tmp_path / "out", v.id, tmp_path / "nope.ogg", clock)


@needs_ffmpeg
def test_refuses_a_file_with_no_audio(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = scripted(conn)
    junk = tmp_path / "take.ogg"
    junk.write_bytes(b"this is not audio")
    with pytest.raises(voice.VoiceError):
        voice.import_take(conn, tmp_path / "out", v.id, junk, clock)
    assert "voice" not in store_videos.stage_runs(conn, v.id)


@needs_ffmpeg
def test_imports_cleans_and_records(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = scripted(conn)
    take = tmp_path / "take.ogg"
    # 1 s of silence, 4 s of tone, 1 s of silence: the silence is trimmed.
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S607
         "aevalsrc=if(between(t\\,1\\,5)\\,0.3*sin(2*PI*220*t)\\,0):s=48000:d=6",
         "-c:a", "libopus", str(take)],
        check=True,
    )  # fmt: skip
    got = voice.import_take(conn, tmp_path / "out", v.id, take, clock)
    assert got.path == tmp_path / "out" / v.id / "voice.wav"
    assert 3.5 < got.seconds < 4.6
    assert store_videos.stage_runs(conn, v.id)["voice"].path == "voice.wav"
    assert not (tmp_path / "out" / v.id / "voice.tmp.wav").exists()
    assert not (tmp_path / "out" / v.id / "voice.trim.wav").exists()


@needs_ffmpeg
def test_a_take_with_no_silence_to_trim_does_not_hang(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ffmpeg 6.1 never finishes silenceremove + areverse + loudnorm in one chain
    # when there is no silence to trim (a take Tara cut tight, or a noisy room).
    monkeypatch.setattr(voice, "TIMEOUT_S", 15)
    v = scripted(conn)
    take = tmp_path / "take.wav"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S607
         "sine=frequency=200:duration=6", str(take)],
        check=True,
    )  # fmt: skip
    got = voice.import_take(conn, tmp_path / "out", v.id, take, clock)
    assert 5.5 < got.seconds < 6.5


@needs_ffmpeg
@pytest.mark.parametrize("seconds", ["5", "0.05"])
def test_a_silent_or_empty_take_is_refused_clearly(
    conn: sqlite3.Connection, tmp_path: Path, seconds: str
) -> None:
    v = scripted(conn)
    take = tmp_path / "take.wav"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S607
         "anullsrc=r=48000:cl=mono", "-t", seconds, str(take)],
        check=True,
    )  # fmt: skip
    with pytest.raises(voice.VoiceError, match="silent"):
        voice.import_take(conn, tmp_path / "out", v.id, take, clock)
    assert list((tmp_path / "out" / v.id).iterdir()) == []
    assert "voice" not in store_videos.stage_runs(conn, v.id)
