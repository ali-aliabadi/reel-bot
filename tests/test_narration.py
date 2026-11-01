"""Kokoro as the daily videos' voice (REEL_BOT_NARRATION=kokoro, the default)."""

import math
import random
import sqlite3
import struct
import subprocess
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from reel_bot import (
    config,
    daily,
    daily_run,
    guide,
    narration,
    pipeline,
    store,
    store_daily,
    store_videos,
    stories,
    videos,
    voice,
)
from reel_bot.relay_api import Answer
from test_daily import (
    NOW,
    Calls,
    FakeRelay,
    button,
    make_run,
    needs_ffmpeg,
    pasted,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, stories.load_builtin())
    return c


def tone(seconds: float = 6.0) -> guide.Speak:
    """A Speak that writes a tone (not silence, which the voice stage refuses) and
    counts its calls."""

    def speak(text: str, out: Path) -> None:
        speak.calls.append(text)  # type: ignore[attr-defined]
        rate = 24000
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(seconds * rate))
        )
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)

    speak.calls = []  # type: ignore[attr-defined]
    return speak


def kokoro_run(
    conn: sqlite3.Connection, tmp_path: Path, relay: FakeRelay, speak: guide.Speak | None
) -> daily_run.Run:
    base = make_run(conn, tmp_path / "out", relay, Calls())

    def context(vid: str) -> pipeline.Context:
        return replace(base.context(vid), speak=None if speak is None else lambda: speak)

    return replace(base, context=context, narration=narration.KOKORO)


def scripted(run: daily_run.Run, relay: FakeRelay) -> str:
    vid = str(daily.start(run, "fable", "short", random.Random(1)))
    relay.answer(1, button(relay))
    daily.tick(run)
    relay.answer(2, pasted())
    return vid


def test_narration_setting_defaults_to_kokoro() -> None:
    assert config.narration({}) == "kokoro"
    assert config.narration({"REEL_BOT_NARRATION": " Tara "}) == "tara"
    with pytest.raises(config.ConfigError, match="kokoro or tara"):
        config.narration({"REEL_BOT_NARRATION": "elevenlabs"})


def test_the_post_discloses_an_ai_voice() -> None:
    asset = videos.Asset("voice.wav", narration.KIND, narration.SOURCE, narration.LICENCE, NOW)
    assert narration.ai_voice([asset])
    assert not narration.ai_voice([replace(asset, kind="font")])


def test_assets_from_before_the_ai_voice_kind_survive_the_migration(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    c = sqlite3.connect(db, isolation_level=None)
    for n, sql in enumerate(store.MIGRATIONS[:-1], start=1):
        c.executescript(f"BEGIN; {sql}; PRAGMA user_version = {n}; COMMIT;")
    at = NOW.isoformat()
    c.execute(
        "INSERT INTO videos (id, created_at, framing, length) VALUES ('v', ?, 'fable', 'short')",
        (at,),
    )
    c.execute("INSERT INTO assets VALUES ('v', 'font', 'font', 'DejaVu', 'free', ?)", (at,))
    c.close()
    conn = store.open_db(db)
    assert [a.path for a in store_videos.assets(conn, "v")] == ["font"]
    store_videos.add_asset(conn, "v", videos.Asset("voice.wav", "ai-voice", "k", "l", NOW))


def test_without_kokoro_the_video_errors_instead_of_asking_tara(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = kokoro_run(conn, tmp_path, relay, None)
    vid = scripted(run, relay)
    (line,) = daily.tick(run)
    assert line == f"{vid}\terror 1 of 3, trying again next tick: no Kokoro to read the narration"
    assert len(relay.sent) == 2  # the pick and the script: Tara is never asked for a take
    assert "take" not in store_daily.steps(conn, vid)
    assert any(f"{vid}\t" in u and "the voice" in u for u in daily.unfinished(conn))


def test_a_kokoro_failure_is_retried_on_the_next_tick(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    def broken(text: str, out: Path) -> None:
        raise guide.GuideError("Kokoro exited 1: out of memory")

    relay = FakeRelay()
    run = kokoro_run(conn, tmp_path, relay, broken)
    vid = scripted(run, relay)
    (line,) = daily.tick(run)
    assert line == f"{vid}\terror 1 of 3, trying again next tick: Kokoro exited 1: out of memory"
    assert not (tmp_path / "out" / vid / narration.FILENAME).exists()


@needs_ffmpeg
def test_kokoro_reads_the_script_once_and_tara_only_makes_the_pictures(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    png = tmp_path / "p.png"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",  # noqa: S607
         "-i", "color=c=teal:s=1024x1536", "-frames:v", "1", str(png)],
        check=True,
    )  # fmt: skip
    speak, relay = tone(), FakeRelay()
    run = kokoro_run(conn, tmp_path, relay, speak)
    vid = scripted(run, relay)
    assert daily.tick(run) == [f"{vid}\tkokoro read the script"]
    assert len(relay.sent) == 2  # no voice-note question to Tara
    assert "voice" in store_videos.stage_runs(conn, vid)
    (asset,) = [a for a in store_videos.assets(conn, vid) if a.kind == narration.KIND]
    assert (asset.path, asset.licence) == (voice.FILENAME, narration.LICENCE)
    folder = tmp_path / "out" / vid
    assert speak.calls == [(folder / pipeline.READ_FILE).read_text("utf-8")]  # type: ignore[attr-defined]

    assert daily.tick(run) == [f"{vid}\tasked for picture 1"]
    for n in (3, 4, 5):
        relay.answer(n, Answer("tara", "", None, "", png.read_bytes()))
        assert daily.tick(run) == [f"{vid}\tasked for picture {n - 1}"]
    relay.answer(6, Answer("tara", "", None, "", png.read_bytes()))
    (line,) = daily.tick(run)
    assert line.startswith(f"{vid}\tsent ")
    copy = [b["text"] for b in relay.last()["blocks"][2:]]
    assert copy[:2] == list(daily.AI_LABEL)
    description = copy[5]
    assert "The voice and pictures are made with AI." in description
    assert "Narrated by Tara" not in description
    assert len(speak.calls) == 1  # type: ignore[attr-defined]  # never read twice


@needs_ffmpeg
def test_narration_reuses_kokoros_reading_on_disk(conn: sqlite3.Connection, tmp_path: Path) -> None:
    speak, relay = tone(), FakeRelay()
    run = kokoro_run(conn, tmp_path, relay, speak)
    vid = scripted(run, relay)
    daily.tick(run)
    ctx = run.context(vid)
    narration.make(conn, ctx.out_dir, vid, speak, run.clock)
    assert len(speak.calls) == 1  # type: ignore[attr-defined]
