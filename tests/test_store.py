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
