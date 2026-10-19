import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import store, store_videos, videos
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LION_K = Story("kalilah-lion", "lion-and-hare", "The Lion and the Hare", "Kalilah", "S", "R")
LION_P = Story("panchatantra-lion", "lion-and-hare", "The Lion and the Hare", "Panch", "S", "R")
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "S", "R")


def _rand(n: int) -> bytes:
    return bytes(range(n))


def test_new_id_is_a_sortable_ulid() -> None:
    a = videos.new_id(NOW, _rand)
    b = videos.new_id(NOW + timedelta(milliseconds=1), _rand)
    assert videos.ID.match(a)
    assert a < b
    assert a == videos.new_id(NOW, _rand)
    assert videos.new_id(NOW) != videos.new_id(NOW)


@pytest.mark.parametrize(
    ("framing", "length", "ok"),
    [
        ("fable", "short", True),
        ("lesson", "long", True),
        ("moral", "short", False),
        ("fable", "60s", False),
    ],
)
def test_check_settings(framing: str, length: str, ok: bool) -> None:
    if ok:
        videos.check_settings(framing, length)
    else:
        with pytest.raises(videos.VideoError):
            videos.check_settings(framing, length)


@pytest.mark.parametrize("bad", ["../etc", "vid_..", "vid_short", "/etc/x", ""])
def test_video_dir_refuses_paths_that_are_not_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(videos.VideoError):
        videos.video_dir(tmp_path, bad)


def test_video_dir_is_created_under_out(tmp_path: Path) -> None:
    vid = videos.new_id(NOW, _rand)
    path = videos.video_dir(tmp_path / "out", vid)
    assert path == tmp_path / "out" / vid
    assert path.is_dir()


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [LION_K, LION_P, FOX])
    return c


def _video(conn: sqlite3.Connection, options: tuple[str, ...] = ()) -> Video:
    v = Video(videos.new_id(NOW, _rand), NOW, "fable", "short", options)
    store_videos.add_video(conn, v)
    return v


def test_video_round_trips(conn: sqlite3.Connection) -> None:
    v = _video(conn, ("kalilah-lion", "aesop-fox"))
    assert store_videos.get_video(conn, v.id) == v


def test_unknown_video(conn: sqlite3.Connection) -> None:
    with pytest.raises(store_videos.UnknownVideoError):
        store_videos.get_video(conn, "vid_missing")


def test_choose_records_the_pick_and_spends_the_tale(conn: sqlite3.Connection) -> None:
    v = _video(conn, ("kalilah-lion", "aesop-fox"))
    store_videos.choose_story(conn, v.id, "kalilah-lion", "ali", NOW)
    got = store_videos.get_video(conn, v.id)
    assert (got.story_id, got.chosen_by, got.chosen_at) == ("kalilah-lion", "ali", NOW)
    assert store.used_tales(conn) == {"lion-and-hare"}


@pytest.mark.parametrize(
    ("story", "by", "match"),
    [("panchatantra-lion", "ali", "not one of the options"), ("kalilah-lion", " ", "who made it")],
)
def test_choose_refuses_bad_picks(
    conn: sqlite3.Connection, story: str, by: str, match: str
) -> None:
    v = _video(conn, ("kalilah-lion", "aesop-fox"))
    with pytest.raises(store_videos.PickError, match=match):
        store_videos.choose_story(conn, v.id, story, by, NOW)
    assert store_videos.get_video(conn, v.id).story_id is None
    assert store.used_tales(conn) == set()


def test_choose_twice_is_refused(conn: sqlite3.Connection) -> None:
    v = _video(conn)
    store_videos.choose_story(conn, v.id, "aesop-fox", "ali", NOW)
    with pytest.raises(store_videos.PickError, match="already"):
        store_videos.choose_story(conn, v.id, "kalilah-lion", "ali", NOW)
    with pytest.raises(store_videos.PickError, match="already"):
        store_videos.set_options(conn, v.id, ["kalilah-lion"])


def test_a_spent_tale_leaves_the_video_unpicked(conn: sqlite3.Connection) -> None:
    store.mark_used(conn, "panchatantra-lion", "vid_old", NOW)
    v = _video(conn)
    with pytest.raises(store.TaleAlreadyUsedError):
        store_videos.choose_story(conn, v.id, "kalilah-lion", "ali", NOW)
    assert store_videos.get_video(conn, v.id).story_id is None


def test_the_pick_is_all_or_nothing_in_sql(conn: sqlite3.Connection) -> None:
    v = _video(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE videos SET story_id = 'aesop-fox' WHERE id = ?", (v.id,))


def test_set_options(conn: sqlite3.Connection) -> None:
    v = _video(conn)
    store_videos.set_options(conn, v.id, ["aesop-fox"])
    assert store_videos.get_video(conn, v.id).story_options == ("aesop-fox",)
    with pytest.raises(store_videos.UnknownVideoError):
        store_videos.set_options(conn, "vid_missing", [])


def test_stage_runs_are_stored_and_replaced(conn: sqlite3.Connection) -> None:
    v = _video(conn)
    first = StageRun("script", "script.json", 0.02, 4.5, NOW)
    store_videos.record_stage(conn, v.id, first)
    again = StageRun("script", "script.json", 0.03, 5.0, NOW + timedelta(hours=1))
    store_videos.record_stage(conn, v.id, again)
    assert store_videos.stage_runs(conn, v.id) == {"script": again}
    with pytest.raises(store_videos.UnknownVideoError):
        store_videos.record_stage(conn, "vid_missing", first)
