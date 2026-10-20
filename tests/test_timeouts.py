"""Every external process and network call gives up after its timeout instead of
hanging the run (the rule in .claude/skills/reel-bot-review)."""

import shutil
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from reel_bot import openai_api, relay_api, render, render_write, voice, web

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed; the process timeout tests need it"
)
ENDLESS = [
    "ffmpeg",
    "-nostdin",
    "-loglevel",
    "error",
    "-f",
    "lavfi",
    "-i",
    "sine",
    "-f",
    "null",
    "-",
]


class _Slow(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        time.sleep(1.0)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def slow_api() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_openai_calls_time_out_and_retry_a_bounded_number_of_times(slow_api: str) -> None:
    slept: list[float] = []
    client = openai_api.Client("k", slow_api, timeout_s=0.2, sleep=slept.append)
    started = time.monotonic()
    with pytest.raises(openai_api.OpenAIError, match="failed"):
        client.post("/x", b"{}", "application/json", "test call")
    assert slept == [2.0, 4.0]  # three attempts in all
    assert time.monotonic() - started < 3


@needs_ffmpeg
def test_render_ffmpeg_times_out(tmp_path: Path) -> None:
    with pytest.raises(render.RenderError, match="took over"):
        render_write._run(ENDLESS, tmp_path, 0.5)  # pyright: ignore[reportPrivateUsage]


@needs_ffmpeg
def test_voice_ffmpeg_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice, "TIMEOUT_S", 0.5)
    with pytest.raises(voice.VoiceError, match="took over"):
        voice._run(ENDLESS)  # pyright: ignore[reportPrivateUsage]


def test_platform_calls_time_out_and_retry_a_bounded_number_of_times(slow_api: str) -> None:
    slept: list[float] = []
    net = web.Web(timeout_s=0.2, sleep=slept.append)
    with pytest.raises(web.WebError, match="failed"):
        net.send(web.Request("POST", slow_api + "/x", "test call"))
    assert slept == [2.0, 4.0]  # three attempts in all
    slept.clear()
    with pytest.raises(web.WebError, match="failed"):
        net.send(web.Request("POST", slow_api + "/x", "a post", retry=False))
    assert slept == []  # a call that creates a post is tried once


def test_relay_calls_time_out_and_retry_a_bounded_number_of_times(slow_api: str) -> None:
    slept: list[float] = []
    relay = relay_api.Client(slow_api, "k", "reel-bot", web.Web(0.2, slept.append))
    with pytest.raises(relay_api.RelayError, match="failed"):
        relay.send({"to": ["ali"], "text": "hi"}, "key-1")
    assert slept == [2.0, 4.0]  # three attempts; the idempotency key makes retries safe
