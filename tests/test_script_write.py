import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import anthropic
import pytest

from reel_bot import script, script_write, store, store_videos, videos
from reel_bot.script_write import Reply
from reel_bot.stories import Story
from reel_bot.videos import Video

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")


def good(lesson: str = "") -> str:
    return json.dumps(
        {
            "hook": "A hungry fox found grapes he could not reach.",
            "paragraphs": [{"text": " ".join(["w"] * 25), "picture": "A fox under a vine."}] * 4,
            "lesson": lesson,
        }
    )


class Fake:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.users: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any]) -> Reply:
        assert schema is script.SCHEMA
        assert "fables" in system
        self.users.append(user)
        return Reply(self.texts.pop(0), "claude-opus-5-5", 1000, 500)


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=3)
        return self.t


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [FOX])
    return c


def picked(conn: sqlite3.Connection, framing: str = "fable") -> Video:
    v = Video(videos.new_id(NOW), NOW, framing, "short")
    store_videos.add_video(conn, v)
    store_videos.choose_story(conn, v.id, FOX.id, "ali", NOW)
    return v


def test_writes_caches_and_records_cost(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = picked(conn)
    fake = Fake(good())
    s = script_write.write(conn, tmp_path / "out", v.id, fake, Clock())
    assert s.story_id == FOX.id
    assert (tmp_path / "out" / v.id / "script.json").is_file()
    run = store_videos.stage_runs(conn, v.id)["script"]
    assert run.cost_usd == pytest.approx(0.014)  # 1000 in at $4/M + 500 out at $20/M
    assert run.seconds == 3
    # A rerun pays nothing: the fake has no replies left.
    assert script_write.write(conn, tmp_path / "out", v.id, fake, Clock()) == s


def test_add_records_a_usable_pasted_script_for_free(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    v = picked(conn)
    out = tmp_path / "out"
    assert script_write.add(conn, out, v.id, "not json", Clock()) == [
        "it is not the JSON asked for"
    ]
    long = good().replace('"w w', '"' + "w " * 40 + "w", 1)
    assert script_write.add(conn, out, v.id, long, Clock()) == [
        "it has 148 words; it needs 95 to 120"
    ]
    assert "script" not in store_videos.stage_runs(conn, v.id)
    assert script_write.add(conn, out, v.id, f"```json\n{good()}\n```", Clock()) == []
    assert store_videos.stage_runs(conn, v.id)["script"].cost_usd == 0
    # write reuses it: the fake has no replies.
    assert script_write.write(conn, out, v.id, Fake(), Clock()).story_id == FOX.id


def test_add_needs_a_pick(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    with pytest.raises(script.ScriptError, match="no picked story"):
        script_write.add(conn, tmp_path, v.id, good(), Clock())


def test_retries_once_with_the_problems(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = picked(conn)
    fake = Fake(good(lesson="Do not sulk."), good())
    script_write.write(conn, tmp_path / "out", v.id, fake, Clock())
    assert "there must be no lesson" in fake.users[1]
    assert store_videos.stage_runs(conn, v.id)["script"].cost_usd == pytest.approx(0.028)


def test_gives_up_after_two_bad_drafts(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = picked(conn, framing="lesson")
    with pytest.raises(script.ScriptError, match="after 2 tries"):
        script_write.write(conn, tmp_path / "out", v.id, Fake(good(), good()), Clock())
    assert store_videos.stage_runs(conn, v.id) == {}
    assert not (tmp_path / "out" / v.id / "script.json").exists()


def test_needs_a_human_pick(conn: sqlite3.Connection, tmp_path: Path) -> None:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    with pytest.raises(script.ScriptError, match="no picked story"):
        script_write.write(conn, tmp_path / "out", v.id, Fake(), Clock())


def test_reply_cost_prices_unknown_models_high() -> None:
    assert Reply("", "claude-mystery", 1_000_000, 0).cost_usd() == 5.0


class _FakeAPI(BaseHTTPRequestHandler):
    """Answers POST /v1/messages like the Claude API, and keeps the last request."""

    last: dict[str, Any] = {}  # noqa: RUF012
    stop_reason = "end_turn"

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).last = {
            "path": self.path,
            "body": body,
            "beta": self.headers.get("anthropic-beta"),
        }
        out = json.dumps(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": good()}],
                "stop_reason": type(self).stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def api() -> Iterator[anthropic.Anthropic]:
    server = HTTPServer(("127.0.0.1", 0), _FakeAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield anthropic.Anthropic(
        api_key="test-key", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0
    )
    server.shutdown()


def test_claude_adapter_sends_the_request(api: anthropic.Anthropic) -> None:
    _FakeAPI.stop_reason = "end_turn"
    reply = script_write.claude(api)("sys", "user", script.SCHEMA)
    assert reply == Reply(good(), "claude-opus-5-5", 10, 20)
    body = _FakeAPI.last["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["format"]["schema"] == script.SCHEMA
    assert script_write.FALLBACK_BETA in _FakeAPI.last["beta"]


def test_claude_adapter_refuses_a_cut_off_reply(api: anthropic.Anthropic) -> None:
    _FakeAPI.stop_reason = "refusal"
    with pytest.raises(script.ScriptError, match="refusal"):
        script_write.claude(api)("sys", "user", script.SCHEMA)
