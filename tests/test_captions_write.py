import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

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
        self.prompts: list[str] = []

    def __call__(self, audio: Path, prompt: str) -> Heard:
        assert audio.name == "voice.wav"
        self.prompts.append(prompt)
        return Heard(tuple(Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(self.words)), 60)


def test_makes_caches_and_records_cost(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v, s = voiced(conn, tmp_path / "out")
    fake = Fake([w.strip(".,").lower() for w in s.spoken().split()])
    c = captions_write.make(conn, tmp_path / "out", v.id, fake, Clock())
    assert c.matched == 1.0
    assert fake.prompts == [" ".join(s.spoken().split())]
    run = store_videos.stage_runs(conn, v.id)["captions"]
    assert (run.cost_usd, run.seconds) == (0.006, 2)
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


def test_parse_rejects_a_reply_without_times() -> None:
    with pytest.raises(captions.CaptionsError):
        captions_write.parse(b'{"text": "hi"}')
    assert captions_write.parse(b'{"duration": 3, "words": []}') == Heard((), 3.0)


def test_openai_needs_https_and_a_key() -> None:
    with pytest.raises(captions.CaptionsError, match="https"):
        captions_write.openai("k", "http://example.com/v1")
    with pytest.raises(captions.CaptionsError, match="OPENAI_API_KEY"):
        captions_write.openai("")


class _FakeAPI(BaseHTTPRequestHandler):
    """Answers POST /v1/audio/transcriptions like OpenAI; fails the first `fail` calls."""

    fail: list[int] = []  # noqa: RUF012
    seen: list[dict[str, Any]] = []  # noqa: RUF012

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["content-length"]))
        type(self).seen.append(
            {"path": self.path, "auth": self.headers["authorization"], "body": body}
        )
        if type(self).fail:
            self.send_response(type(self).fail.pop(0))
            self.send_header("content-length", "0")
            self.end_headers()
            return
        out = json.dumps(
            {"duration": 30.0, "words": [{"word": " Fox", "start": 0.1, "end": 0.4}]}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def api() -> Iterator[str]:
    _FakeAPI.fail, _FakeAPI.seen = [], []
    server = HTTPServer(("127.0.0.1", 0), _FakeAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_openai_sends_a_multipart_request(api: str, tmp_path: Path) -> None:
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF-audio")
    heard = captions_write.openai("sk-test", api)(audio, "A fox wanted grapes.")
    assert heard == Heard((Word("Fox", 0.1, 0.4),), 30.0)
    seen = _FakeAPI.seen[0]
    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer sk-test"
    for part in (b"whisper-1", b"timestamp_granularities[]", b"verbose_json", b"RIFF-audio"):
        assert part in seen["body"]


def test_openai_retries_server_errors_then_gives_up(api: str, tmp_path: Path) -> None:
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF")
    slept: list[float] = []
    _FakeAPI.fail = [503, 429]
    assert captions_write.openai("k", api, slept.append)(audio, "").seconds == 30.0
    assert slept == [2.0, 4.0]
    _FakeAPI.fail = [500, 500, 500]
    with pytest.raises(captions.CaptionsError, match="HTTP 500"):
        captions_write.openai("k", api, slept.append)(audio, "")
    _FakeAPI.fail = [401]
    with pytest.raises(captions.CaptionsError, match="HTTP 401"):
        captions_write.openai("k", api, slept.append)(audio, "")


def test_openai_reports_a_dead_server(tmp_path: Path) -> None:
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF")
    t = captions_write.openai("k", "http://127.0.0.1:9/v1", lambda _: None)
    with pytest.raises(captions.CaptionsError, match="URLError"):
        t(audio, "")
