import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reel_bot import store
from reel_bot.stories import Story

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LION_K = Story("kalilah-lion", "lion-and-hare", "The Lion and the Hare", "Kalilah", "S", "R")
LION_P = Story("panchatantra-lion", "lion-and-hare", "The Lion and the Hare", "Panch", "S", "R")
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "S", "R")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "data" / "reel-bot.db")
    store.sync_stories(c, [LION_K, LION_P, FOX])
    return c


def test_open_db_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "reel-bot.db"
    store.open_db(path).close()
    c = store.open_db(path)
    assert c.execute("PRAGMA user_version").fetchone() == (len(store.MIGRATIONS),)


def test_sync_updates_and_lists(conn: sqlite3.Connection) -> None:
    renamed = replace(FOX, title="Sour Grapes")
    store.sync_stories(conn, [renamed])
    assert store.all_stories(conn) == [renamed, LION_K, LION_P]


def test_mark_used(conn: sqlite3.Connection) -> None:
    store.mark_used(conn, LION_K.id, "vid_1", NOW)
    assert store.used_tales(conn) == {"lion-and-hare"}
    row = conn.execute("SELECT story_id, video_id, used_at FROM tales_used").fetchone()
    assert row == ("kalilah-lion", "vid_1", "2026-10-22T09:00:00+00:00")


def test_same_tale_in_another_collection_is_refused(conn: sqlite3.Connection) -> None:
    store.mark_used(conn, LION_K.id, "vid_1", NOW)
    with pytest.raises(store.TaleAlreadyUsedError, match="vid_1"):
        store.mark_used(conn, LION_P.id, "vid_2", NOW)
    assert conn.execute("SELECT count(*) FROM tales_used").fetchone() == (1,)


def test_unknown_story_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(store.UnknownStoryError):
        store.mark_used(conn, "nope", "vid_1", NOW)


def test_offered_tales(conn: sqlite3.Connection) -> None:
    assert store.offered_tales(conn) == set()
    conn.execute(
        "INSERT INTO videos (id, created_at, framing, length, story_options)"
        " VALUES ('vid_1', ?, 'fable', 'short', ?)",
        (NOW.isoformat(), '["panchatantra-lion", "gone"]'),
    )
    assert store.offered_tales(conn) == {"lion-and-hare"}


def test_upgrade_preserves_daily_answers_and_accepts_fourteen_pictures(tmp_path: Path) -> None:
    c = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    c.execute("PRAGMA foreign_keys = ON")
    for version, sql in enumerate(store.MIGRATIONS[:-1], 1):
        c.executescript(f"BEGIN; {sql}; PRAGMA user_version = {version}; COMMIT;")
    c.execute(
        "INSERT INTO videos (id, created_at, framing, length) VALUES (?, ?, ?, ?)",
        ("vid_1", NOW.isoformat(), "fable", "long"),
    )
    c.execute(
        "INSERT INTO daily_steps VALUES (?, ?, ?, ?, ?)",
        ("vid_1", "picture-6", "msg_saved", NOW.isoformat(), 2),
    )
    before = c.execute("SELECT * FROM daily_steps").fetchall()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO daily_steps VALUES (?, ?, ?, ?, ?)",
            ("vid_1", "picture-7", "msg_7", NOW.isoformat(), 1),
        )
    store.migrate(c)
    store.migrate(c)
    assert c.execute("SELECT * FROM daily_steps").fetchall() == before
    for n in range(7, 15):
        c.execute(
            "INSERT INTO daily_steps VALUES (?, ?, ?, ?, ?)",
            ("vid_1", f"picture-{n}", f"msg_{n}", NOW.isoformat(), 1),
        )
    for bad in ("picture-0", "picture-15", "picture-100", "picture-01", "script-4"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO daily_steps VALUES (?, ?, ?, ?, ?)",
                ("vid_1", bad, "msg_bad", NOW.isoformat(), 1),
            )
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("SELECT count(*) FROM daily_steps").fetchone() == (9,)
