import base64
import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from reel_bot import script, store, store_videos, videos, visuals, visuals_write
from reel_bot.script import Paragraph, Script
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video
from reel_bot.visuals_write import Drawn

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
PNG = visuals.PNG_MAGIC + b"picture"


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


def scripted(conn: sqlite3.Connection, out: Path, with_script: bool = True) -> Video:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    if with_script:
        store_videos.choose_story(conn, v.id, FOX.id, "ali", NOW)
        s = Script(
            v.id, FOX.id, FOX.title, "A fox wanted grapes.",
            tuple(Paragraph(f"Scene {i}.", f"A fox, scene {i}.") for i in range(1, 4)),
            "", FOX.source, FOX.source_ref,
        )  # fmt: skip
        folder = videos.video_dir(out, v.id)
        (folder / "script.json").write_text(script.to_json(s), encoding="utf-8")
        store_videos.record_stage(conn, v.id, StageRun("script", "script.json", 0.01, 1, NOW))
    return v


class Fake:
    def __init__(self, fail_at: int = 0, png: bytes = PNG) -> None:
        self.prompts: list[str] = []
        self.fail_at = fail_at
        self.png = png

    def __call__(self, prompt: str) -> Drawn:
        self.prompts.append(prompt)
        if len(self.prompts) == self.fail_at:
            raise visuals.VisualsError("picture failed: HTTP 500")
        return Drawn(self.png, 0.05)


def test_draws_records_assets_and_reuses(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    fake = Fake()
    plan = visuals_write.make(conn, out, v.id, fake, Clock())
    assert len(fake.prompts) == 3
    assert "A fox, scene 2." in fake.prompts[1]
    assert (out / v.id / "picture-3.png").read_bytes() == PNG
    manifest = json.loads((out / v.id / "visuals.json").read_text(encoding="utf-8"))
    assert [p["section"] for p in manifest["pictures"]] == ["p1", "p2", "p3"]
    assert store_videos.stage_runs(conn, v.id)["visuals"].cost_usd == pytest.approx(0.15)
    assets = store_videos.assets(conn, v.id)
    assert [(a.path, a.kind, a.source) for a in assets] == [
        (f"picture-{i}.png", "image", "openai:gpt-image-1") for i in range(1, 4)
    ]
    assert "commercial use" in assets[0].licence
    assert visuals_write.make(conn, out, v.id, Fake(fail_at=1), Clock()) == plan


def test_a_rerun_draws_only_the_missing_pictures(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    with pytest.raises(visuals.VisualsError, match="HTTP 500"):
        visuals_write.make(conn, out, v.id, Fake(fail_at=2), Clock())
    assert "visuals" not in store_videos.stage_runs(conn, v.id)
    again = Fake()
    visuals_write.make(conn, out, v.id, again, Clock())
    assert len(again.prompts) == 2
    assert store_videos.stage_runs(conn, v.id)["visuals"].cost_usd == pytest.approx(0.1)


def test_refuses_a_non_png_and_a_video_without_a_script(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    with pytest.raises(visuals.VisualsError, match="not a PNG"):
        visuals_write.make(conn, out, v.id, Fake(png=b"<html>"), Clock())
    assert not (out / v.id / "picture-1.png").exists()
    bare = scripted(conn, out, with_script=False)
    with pytest.raises(visuals.VisualsError, match="no script"):
        visuals_write.make(conn, out, bare.id, Fake(), Clock())


def test_parse_rejects_a_reply_without_a_picture() -> None:
    with pytest.raises(visuals.VisualsError):
        visuals_write.parse(b'{"data": []}')


def test_openai_needs_https_and_a_key() -> None:
    with pytest.raises(visuals.VisualsError, match="https"):
        visuals_write.openai("k", "http://example.com/v1")
    with pytest.raises(visuals.VisualsError, match="OPENAI_API_KEY"):
        visuals_write.openai("")


class _FakeAPI(BaseHTTPRequestHandler):
    """Answers POST /v1/images/generations like OpenAI; fails the first `fail` calls."""

    fail: list[int] = []  # noqa: RUF012
    seen: list[dict[str, Any]] = []  # noqa: RUF012

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append({"path": self.path, "auth": self.headers["authorization"], **body})
        if type(self).fail:
            self.send_response(type(self).fail.pop(0))
            self.send_header("content-length", "0")
            self.end_headers()
            return
        out = json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode()
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


def test_openai_asks_for_one_portrait_picture(api: str) -> None:
    slept: list[float] = []
    _FakeAPI.fail = [503]
    drawn = visuals_write.openai("sk-test", api, slept.append)("A fox under a vine.")
    assert drawn == Drawn(PNG, visuals_write.PRICE_PER_IMAGE)
    assert slept == [2.0]
    seen = _FakeAPI.seen[-1]
    assert seen["path"] == "/v1/images/generations"
    assert seen["auth"] == "Bearer sk-test"
    assert (seen["model"], seen["size"], seen["n"]) == ("gpt-image-1", "1024x1536", 1)
    _FakeAPI.fail = [400]
    with pytest.raises(visuals.VisualsError, match="HTTP 400"):
        visuals_write.openai("k", api, slept.append)("x")
