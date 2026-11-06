import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reel_bot import (
    cli_progress,
    progress_io,
    progress_view,
    relay_progress,
    script,
    store,
    store_progress,
    store_videos,
    videos,
    visuals,
    web,
)
from reel_bot.relay_api import RelayError
from test_progress import NOW, VID, Relay, state


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "db.sqlite"
    conn = store.open_db(path)
    store_videos.add_video(conn, videos.Video(VID, NOW, "fable", "short"))
    conn.execute(
        "INSERT INTO daily_steps (video_id,step,message_id,at) VALUES (?,?,?,?)",
        (VID, "pick", "msg_pick", NOW.isoformat()),
    )
    conn.close()
    for k, v in {
        "RELAY_URL": "https://relay.test",
        "RELAY_API_KEY": "fake-key",
        "RELAY_USER": "tara",
        "REEL_BOT_NARRATOR": "tara",
        "REEL_BOT_DB_PATH": str(path),
        "REEL_BOT_OUT_DIR": str(tmp_path / "out"),
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["progress", "--once"])

    def quiet(_: str) -> None:
        pass

    monkeypatch.setattr(cli_progress.logs, "setup", quiet)
    return path


@pytest.mark.parametrize("mode", ["ready", "old", "down"])
def test_service_capability_gate(
    configured: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    class Client(Relay):
        def supports_edit(self) -> bool:
            if mode == "down":
                raise web.WebError("unavailable")
            return mode == "ready"

    client = Client()

    def make_client(*args: Any) -> Client:
        return client

    monkeypatch.setattr(relay_progress, "ProgressRelay", make_client)
    assert cli_progress.main() == 0
    assert len(client.sent) == (1 if mode == "ready" else 0)


def test_service_loop_interrupt_and_configuration_errors(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client(Relay):
        def supports_edit(self) -> bool:
            return False

    def make_client(*args: Any) -> Client:
        return Client()

    monkeypatch.setattr(relay_progress, "ProgressRelay", make_client)
    monkeypatch.setattr(sys, "argv", ["progress"])

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_progress.time, "sleep", interrupt)
    assert cli_progress.main() == 0
    monkeypatch.delenv("REEL_BOT_NARRATOR")
    assert cli_progress.main() == 2
    monkeypatch.delenv("RELAY_API_KEY")
    assert cli_progress.main() == 2


def test_tick_defers_errors_without_changing_the_cycle(configured: Path) -> None:
    class Broken:
        def update(self, *args: Any, **kwargs: Any) -> bool:
            raise web.WebError("unavailable")

    with store_progress.connect(configured) as conn:
        before = conn.total_changes
        cli_progress.tick(Broken(), conn, VID)  # type: ignore[arg-type]
        assert conn.total_changes == before


def test_images_captions_render_and_upload_have_distinct_statuses(tmp_path: Path) -> None:
    s = script.Script(
        VID, "story", "dove", "Hook", (script.Paragraph("text", "dove"),), "", "source", "ref"
    )
    (tmp_path / "script.json").write_text(script.to_json(s))
    saved = state(stages=frozenset({"script", "voice"}))
    view = progress_view.build(saved, tmp_path, True, "kokoro")
    assert "Collecting pictures" in view.stage
    assert "0 of 1" in view.detail
    (tmp_path / "picture-1.png").write_bytes(visuals.PNG_MAGIC)
    view = progress_view.build(saved, tmp_path, True, "kokoro")
    assert "captions" in view.stage

    progress_io.write(tmp_path, "captions", 5, 10)
    view = progress_view.build(saved, tmp_path, True, "kokoro")
    assert "50%" in progress_view.text("dove", view, NOW)
    progress_io.write(tmp_path, "render", 0, 10)
    (tmp_path / "render.progress.txt").write_text("out_time_us=7500000\n")
    rendering = state(stages=frozenset({"script", "voice", "captions"}))
    view = progress_view.build(rendering, tmp_path, True, "kokoro")
    assert "75%" in progress_view.text("dove", view, datetime.now(UTC))
    (tmp_path / "render.progress.txt").unlink()
    assert progress_view.build(rendering, tmp_path, True, "kokoro").done == 0
    assert (
        "Sending"
        in progress_view.build(
            state(stages=frozenset({"script", "voice", "captions", "render"})),
            tmp_path,
            True,
            "kokoro",
        ).stage
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"channels":[{"name":"telegram","healthy":true,"edit_text":true}]}', True),
        (b'{"channels":[{"name":"telegram","healthy":true}]}', False),
        (b'{"channels":[{"name":"telegram","healthy":false,"edit_text":true}]}', False),
        (b'{"channels":[{"name":"email"},null,5]}', False),
        (b"{}", False),
        (b"null", False),
        (b"bad", False),
    ],
)
def test_capability_parser_handles_old_and_malformed_replies(body: bytes, expected: bool) -> None:
    class Net:
        def send(self, req: web.Request) -> web.Reply:
            assert req.method == "GET"
            return web.Reply(200, {}, body)

        def wait(self, seconds: float) -> None:
            pass

    relay = relay_progress.ProgressRelay("https://relay.test", "k", "reel-bot", Net())
    assert relay.supports_edit() is expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"status":"delivered"}', "delivered"),
        (b'{"status":"queued"}', "queued"),
        (b'{"status":"failed"}', "failed"),
        (b"{}", None),
        (b"null", None),
        (b"bad", None),
    ],
)
def test_file_delivery_status_is_validated(body: bytes, expected: str | None) -> None:
    class Net:
        def send(self, req: web.Request) -> web.Reply:
            return web.Reply(200, {}, body)

        def wait(self, seconds: float) -> None:
            pass

    relay = relay_progress.ProgressRelay("https://relay.test", "k", "reel-bot", Net())
    if expected:
        assert relay.message_status("msg_file") == expected
    else:
        with pytest.raises(RelayError):
            relay.message_status("msg_file")
