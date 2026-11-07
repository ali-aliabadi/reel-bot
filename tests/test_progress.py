import json
import sqlite3
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import (
    progress_io,
    progress_live,
    progress_view,
    relay_api,
    relay_progress,
    store,
    store_progress,
    store_videos,
    stories,
    videos,
    web,
)
from reel_bot.store_progress import State

NOW = datetime(2026, 11, 6, 19, 0, tzinfo=UTC)
VID = "vid_01M6S1G447ZJH3Z1TGCTDD7350"


def state(**kwargs: object) -> State:
    base = State(
        VID, "The dove", True, frozenset({"pick", "script-1"}), frozenset({"script"}), 0, NOW
    )
    return replace(base, **kwargs)  # type: ignore[arg-type]


def test_numeric_checkpoints_keep_start_and_reject_bad_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(progress_io.time, "time", lambda: 100.0)
    progress_io.write(tmp_path, "voice", 0, 8)
    monkeypatch.setattr(progress_io.time, "time", lambda: 200.0)
    progress_io.write(tmp_path, "voice", 4, 8)
    assert progress_io.read(tmp_path, "voice") == progress_io.Checkpoint(4, 8, 100, 200)
    progress_io.write(tmp_path, "voice", 0, 8)  # retry resets elapsed time
    assert progress_io.read(tmp_path, "voice").started == 200  # type: ignore[union-attr]
    for raw in (
        "null",
        "[]",
        "bad",
        '{"done":NaN,"total":1,"started":0,"updated":0}',
        '{"done":0,"total":0,"started":0,"updated":0}',
    ):
        (tmp_path / "voice.progress.json").write_text(raw)
        assert progress_io.read(tmp_path, "voice") is None
    with pytest.raises(ValueError, match="unknown"):
        progress_io.write(tmp_path, "../../other", 1, 1)
    progress_io.write(tmp_path / "missing", "voice", 1, 1)  # optional telemetry mustn't fail work


def test_ffmpeg_progress_handles_partial_and_broken_output(tmp_path: Path) -> None:
    assert progress_io.rendered_seconds(tmp_path) == 0
    path = tmp_path / "render.progress.txt"
    path.write_text("out_time_us=1000000\nprogress=continue\nout_time_us=2500000\n")
    assert progress_io.rendered_seconds(tmp_path) == 2.5
    for raw in ("out_time_us=N/A\n", "out_time_us=-50000\n", "progress=continue\n"):
        path.write_text(raw)
        assert progress_io.rendered_seconds(tmp_path) == 0


def test_stage_progress_is_real_and_never_completes_before_validation(tmp_path: Path) -> None:
    progress_io.write(tmp_path, "voice", 4, 8)
    view = progress_view.build(state(), tmp_path, True, "kokoro")
    text = progress_view.text("The dove", view, NOW)
    assert "50%" in text
    assert "4 of 8" in text
    assert "Generating narration" in text
    progress_io.write(tmp_path, "voice", 8, 8)
    assert "99%" in progress_view.text(
        "dove", progress_view.build(state(), tmp_path, True, "kokoro"), NOW
    )
    assert (
        "voice recording"
        in progress_view.build(state(steps=frozenset({"take"})), tmp_path, True, "tara").stage
    )
    assert "voice guide" in progress_view.build(state(), tmp_path, True, "tara").stage
    assert (
        "story choice" in progress_view.build(state(picked=False), tmp_path, True, "kokoro").stage
    )
    assert (
        "script"
        in progress_view.build(state(stages=frozenset[str]()), tmp_path, True, "kokoro").stage
    )


def test_stopped_waiting_and_terminal_states_are_distinct(tmp_path: Path) -> None:
    for step, phrase in (("sent", "Complete"), ("failed", "Stopped"), ("cancelled", "Cancelled")):
        view = progress_view.build(state(steps=frozenset({step})), tmp_path, False, "kokoro")
        assert view.terminal
        assert phrase in view.stage
    assert "Interrupted" in progress_view.build(state(), tmp_path, False, "kokoro").stage
    assert "Retrying" in progress_view.build(state(errors=2), tmp_path, True, "kokoro").stage
    stale = progress_view.View(
        "Rendering", started=NOW.timestamp() - 120, updated=NOW.timestamp() - 200
    )
    text = progress_view.text("dove", stale, NOW)
    assert "2m 00s" in text
    assert "checkpoint" in text
    assert "Updated" in text


class Relay:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.edits: list[tuple[str, str]] = []
        self.fail = False
        self.deleted: list[str] = []

    def supports_delete(self) -> bool:
        return True

    def delete_text(self, message_id: str) -> None:
        if self.fail:
            raise web.WebError("relay unavailable")
        self.deleted.append(message_id)

    def send(self, message: dict[str, object]) -> str:
        self.sent.append(message)
        return f"msg_status_{len(self.sent)}"

    def message_status(self, message_id: str) -> str:
        return "delivered"

    def edit_text(self, message_id: str, text: str) -> None:
        if self.fail:
            raise web.WebError("relay unavailable")
        self.edits.append((message_id, text))


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = store.open_db(tmp_path / "db.sqlite")
    store.sync_stories(conn, stories.load_builtin())
    store_videos.add_video(conn, videos.Video(VID, NOW, "fable", "short"))
    conn.execute(
        "INSERT INTO daily_steps (video_id,step,message_id,at) VALUES (?,?,?,?)",
        (VID, "pick", "msg_pick", NOW.isoformat()),
    )
    return conn


def test_sidecar_edits_one_card_across_restart_and_outage(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = Relay()
    observer = progress_live.Observer(relay, tmp_path, ("tara", "tara"), "kokoro", lambda: True)
    assert store_progress.recent(db) == [VID]
    assert observer.update(db, VID, NOW)
    assert relay.sent[0]["to"] == ["tara"]
    restarted = replace(observer)
    relay.fail = True
    with pytest.raises(web.WebError):
        restarted.update(db, VID, NOW + timedelta(seconds=20))
    relay.fail = False
    assert restarted.update(db, VID, NOW + timedelta(seconds=40))
    assert len(relay.sent) == 1
    assert len(relay.edits) == 1
    db.execute(
        "INSERT INTO daily_steps (video_id,step,message_id,at) VALUES (?,?,?,?)",
        (VID, "sent", "msg_file", NOW.isoformat()),
    )
    assert observer.update(db, VID, NOW)
    assert observer.update(db, VID, NOW)  # confirm delivery, delete the previous card
    assert not observer.update(db, VID, NOW)
    assert "100%" in relay.edits[-1][1]
    # Historical cycles get no unsolicited cards, unless explicitly requested.
    progress_live.card_path(tmp_path / VID).unlink()
    assert not observer.update(db, VID, NOW)
    assert observer.update(db, VID, NOW, force=True)


def test_progress_database_connection_is_read_only(tmp_path: Path, db: sqlite3.Connection) -> None:
    with store_progress.connect(tmp_path / "db.sqlite") as conn:
        assert store_progress.state(conn, VID).video_id == VID
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM videos")
        with pytest.raises(ValueError, match="unknown"):
            store_progress.state(conn, "missing")


def test_card_parser_and_worker_status_fail_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for raw in ("null", "[]", "{}", '{"message_id":3}', "bad"):
        progress_live.card_path(tmp_path).write_text(raw)
        assert progress_live.read_card(tmp_path) is None

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "activating\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert progress_live.worker_active()

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("systemctl", 5)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert not progress_live.worker_active()


def test_relay_edit_has_no_duplicate_send_fallback_and_hides_key() -> None:
    class Net:
        def __init__(self) -> None:
            self.sent: list[web.Request] = []

        def send(self, req: web.Request) -> web.Reply:
            self.sent.append(req)
            return web.Reply(200, {}, b"{}")

        def wait(self, seconds: float) -> None:
            pass

    net = Net()
    relay = relay_progress.ProgressRelay("https://relay.test", "private-key", "reel-bot", net)
    relay.edit_text("msg_status", "42%")
    (req,) = net.sent
    assert req.method == "PATCH"
    assert not req.retry
    assert json.loads(req.body) == {"text": "42%"}
    assert "private-key" not in repr(relay)
    with pytest.raises(relay_api.RelayError):
        relay.edit_text("msg_../../other", "42%")


def test_card_waits_for_actual_file_delivery_and_reports_failure() -> None:
    complete = progress_view.View("Complete", done=1, total=1, terminal=True)
    for status in ("queued", "sending"):
        result = progress_live.delivered_view(complete, status)
        assert not result.terminal
        assert "Delivering" in result.stage
    assert progress_live.delivered_view(complete, "delivered") is complete
    for status in ("failed", "partially_delivered"):
        assert "failed" in progress_live.delivered_view(complete, status).stage
