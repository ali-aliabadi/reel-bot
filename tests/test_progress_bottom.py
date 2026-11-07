import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from reel_bot import (
    progress_live,
    relay_api,
    relay_progress,
    store,
    store_progress,
    store_videos,
    stories,
    videos,
    web,
)
from test_progress import NOW, VID, Relay


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = store.open_db(tmp_path / "db.sqlite")
    store.sync_stories(conn, stories.load_builtin())
    store_videos.add_video(conn, videos.Video(VID, NOW, "fable", "short"))
    step(conn, "pick", "msg_pick")
    return conn


def step(conn: sqlite3.Connection, name: str, mid: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO daily_steps (video_id,step,message_id,at) VALUES (?,?,?,?)",
        (VID, name, mid, NOW.isoformat()),
    )


class Queue(Relay):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: dict[str, str] = {}
        self.can_delete = True

    def supports_delete(self) -> bool:
        return self.can_delete

    def message_status(self, message_id: str) -> str:
        return self.statuses.get(message_id, "delivered")


def test_card_follows_delivered_questions_without_reposting_on_each_poll(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = Queue()
    observer = progress_live.Observer(relay, tmp_path, ("tara",), "kokoro", lambda: True)
    relay.statuses["msg_pick"] = "queued"
    assert not observer.update(db, VID, NOW)
    relay.statuses["msg_pick"] = "delivered"
    assert observer.update(db, VID, NOW)
    first = progress_live.read_card(tmp_path / VID)
    assert first is not None
    step(db, "script-1", "msg_script")
    relay.statuses["msg_script"] = "queued"
    observer.update(db, VID, NOW)
    assert len(relay.sent) == 1
    relay.statuses["msg_script"] = "delivered"
    observer.update(db, VID, NOW)
    second = progress_live.read_card(tmp_path / VID)
    assert second
    assert second.message_id != first.message_id
    assert len(relay.sent) == 2
    assert relay.deleted == []
    relay.statuses[second.message_id] = "queued"
    observer.update(db, VID, NOW)
    assert relay.deleted == []  # never remove a live card before replacement arrives
    relay.statuses[second.message_id] = "delivered"
    observer.update(db, VID, NOW)
    assert relay.deleted == [first.message_id]
    for _ in range(3):
        observer.update(db, VID, NOW + timedelta(seconds=20))
    assert len(relay.sent) == 2
    assert all(mid == second.message_id for mid, _ in relay.edits[1:])
    assert store_progress.state(db, VID).latest_message == "msg_script"


def test_old_relay_keeps_editing_until_delete_support_is_available(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = Queue()
    relay.can_delete = False
    observer = progress_live.Observer(relay, tmp_path, ("tara",), "kokoro", lambda: True)
    observer.update(db, VID, NOW)
    step(db, "script-1", "msg_script")
    observer.update(db, VID, NOW)
    assert len(relay.sent) == 1
    assert len(relay.edits) == 1
    relay.can_delete = True
    observer.update(db, VID, NOW)
    assert len(relay.sent) == 2
    relay.fail = True
    # Deletion failure is optional and stays journaled; live card still edits.
    with pytest.raises(web.WebError):
        observer.update(db, VID, NOW)
    relay.fail = False
    replace(observer).update(db, VID, NOW)
    assert relay.deleted == ["msg_status_1"]
    assert progress_live.read_card(tmp_path / VID).retired == ()  # type: ignore[union-attr]


def test_lost_send_reply_reuses_key_even_when_next_step_arrives(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    class Lost(Queue):
        lose = False

        def __init__(self) -> None:
            super().__init__()
            self.accepted: dict[str, str] = {}

        def send(self, message: dict[str, object]) -> str:
            key = str(message["idempotency_key"])
            if key not in self.accepted:
                self.accepted[key] = super().send(message)
            if self.lose:
                self.lose = False
                raise web.WebError("lost reply")
            return self.accepted[key]

    relay = Lost()
    observer = progress_live.Observer(relay, tmp_path, ("tara",), "kokoro", lambda: True)
    observer.update(db, VID, NOW)
    step(db, "script-1", "msg_script")
    relay.lose = True
    with pytest.raises(web.WebError):
        observer.update(db, VID, NOW)
    step(db, "script-2", "msg_fix")
    replace(observer).update(db, VID, NOW)
    assert len(relay.sent) == 2  # recovers the accepted send instead of duplicating it
    observer.update(db, VID, NOW)
    assert len(relay.sent) == 3  # follows the later prompt with its own card
    observer.update(db, VID, NOW)
    assert len(relay.sent) == 3
    assert relay.deleted == ["msg_status_1", "msg_status_2"]


def test_existing_card_moves_once_and_stage_completion_follows_human_reply(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = Queue()
    observer = progress_live.Observer(relay, tmp_path, ("tara",), "kokoro", lambda: True)
    folder = videos.video_dir(tmp_path, VID)
    progress_live.save_card(folder, progress_live.Card("msg_old"))
    observer.update(db, VID, NOW)
    observer.update(db, VID, NOW)
    assert relay.deleted == ["msg_old"]
    assert len(relay.sent) == 1
    store_videos.record_stage(db, VID, videos.StageRun("script", "script.json", 0, 0, NOW))
    observer.update(db, VID, NOW)
    assert len(relay.sent) == 2


@pytest.mark.parametrize("raw", ["null", "[]", "{}", '{"revision":"bad","anchor":4}'])
def test_invalid_pending_journal_defers_without_losing_the_live_card(
    db: sqlite3.Connection, tmp_path: Path, raw: str
) -> None:
    relay = Queue()
    observer = progress_live.Observer(relay, tmp_path, ("tara",), "kokoro", lambda: True)
    observer.update(db, VID, NOW)
    step(db, "script-1", "msg_script")
    (tmp_path / VID / "progress-send.json").write_text(raw)
    with pytest.raises(ValueError, match="invalid progress send journal"):
        observer.update(db, VID, NOW)
    assert len(relay.sent) == 1
    assert relay.deleted == []


def test_delete_client_requires_capability_and_uses_only_an_owned_message_id() -> None:

    class Net:
        sent: list[web.Request]

        def __init__(self) -> None:
            self.sent = []

        def send(self, req: web.Request) -> web.Reply:
            self.sent.append(req)
            return web.Reply(
                200,
                {},
                b'{"channels":[{"name":"telegram","healthy":true,"delete_text":true}]}',
            )

        def wait(self, seconds: float) -> None:
            pass

    net = Net()
    relay = relay_progress.ProgressRelay("https://relay.test", "fake-key", "reel-bot", net)
    assert relay.supports_delete()
    relay.delete_text("msg_status")
    request = net.sent[-1]
    assert request.method == "DELETE"
    assert request.url == "https://relay.test/v1/messages/msg_status/text"
    assert request.body == b""
    assert request.retry is False
    with pytest.raises(relay_api.RelayError):
        relay.delete_text("msg_../../other")
