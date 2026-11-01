import random
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from reel_bot import daily, daily_cancel, store, store_daily, store_videos, stories
from reel_bot.cli import main
from reel_bot.daily_run import DailyError, Run
from reel_bot.relay_api import SKIP, Answer
from test_daily import Calls, Clock, FakeRelay, button, make_run, picked_and_scripted


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, stories.load_builtin())
    return c


def skip(relay: FakeRelay, n: int = 1) -> None:
    assert relay.sent[n - 1]["blocks"][1]["options"][-1] == SKIP
    relay.answer(n, Answer("ali", SKIP, None, ""))


def later(run: Run, hours: float) -> None:
    clock = run.clock
    assert isinstance(clock, Clock)
    clock.t += timedelta(hours=hours)


def test_skip_today_cancels_the_video_without_asking_tara(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = str(daily.start(run, "fable", "short", random.Random(1)))
    skip(relay)
    assert daily.tick(run) == [f"{vid}\tskipped today"]
    assert store_daily.steps(conn, vid)["cancelled"] == ""
    assert store_videos.get_video(conn, vid).story_id is None
    assert len(relay.sent) == 1  # nothing to Tara
    assert daily.tick(run) == []
    assert daily.start(run, "fable", "short", random.Random(2)) is not None


def test_a_pick_unanswered_by_the_next_morning_is_replaced(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    first = str(daily.start(run, "fable", "short", random.Random(1)))
    later(run, 19)
    assert daily.start(run, "fable", "short", random.Random(1)) is None  # same day: wait
    later(run, 2)
    second = daily.start(run, "fable", "short", random.Random(1))
    assert second not in (None, first)
    assert "cancelled" in store_daily.steps(conn, first)
    assert store_daily.open_videos(conn) == [second]
    assert len(relay.sent) == 2  # the new pick only; Tara was never asked


def test_cancelling_after_tara_was_asked_tells_her_and_frees_the_tale(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = picked_and_scripted(run, relay)
    video = store_videos.get_video(conn, vid)
    tale = next(s.tale for s in store.all_stories(conn) if s.id == video.story_id)
    assert tale in store.used_tales(conn)

    daily_cancel.cancel(run, vid, "test")
    note = relay.last()
    assert note["to"] == ["tara"]
    assert note["urgency"] == "low"
    assert note["title"].startswith("Cancelled: ")
    assert tale not in store.used_tales(conn)
    assert store_videos.get_video(conn, vid).chosen_by == "ali"  # the pick is kept
    assert daily.tick(run) == []
    with pytest.raises(DailyError, match="already cancelled"):
        daily_cancel.cancel(run, vid, "test")


def test_only_unsent_daily_videos_can_be_cancelled(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    with pytest.raises(DailyError, match="not a daily video"):
        daily_cancel.cancel(run, "vid_X", "test")
    vid = str(daily.start(run, "fable", "short", random.Random(1)))
    store_daily.add_step(conn, vid, "sent", "msg_9", run.clock())
    with pytest.raises(DailyError, match="already sent"):
        daily_cancel.cancel(run, vid, "test")


def test_list_shows_open_and_failed_videos_and_cancel_all_closes_them(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    waiting = str(daily.start(run, "fable", "short", random.Random(1)))
    relay.answer(1, button(relay))
    daily.tick(run)
    store_daily.add_step(conn, waiting, "failed", "msg_9", run.clock())
    later(run, 21)
    fresh = str(daily.start(run, "fable", "short", random.Random(2)))
    first, second = daily.unfinished(conn)
    assert first.startswith(f"{waiting}\t2026-10-25\t")
    assert first.endswith("\tscript (failed)")
    assert second == f"{fresh}\t2026-10-26\t-\tstory pick"


def test_cli_list_and_cancel(
    conn: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {
        "REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db"),
        "RELAY_URL": "https://relay.example.test",
        "RELAY_API_KEY": "rk_test_not_a_real_key",
        "REEL_BOT_NARRATOR": "tara",
    }
    assert main(["daily", "list"], env) == 0
    assert capsys.readouterr().out == ""
    assert main(["daily", "cancel"], env) == 3
    assert "or pass --all" in capsys.readouterr().err
    assert main(["daily", "cancel", "vid_X", "--all"], env) == 3
    assert main(["daily", "cancel", "--all"], env) == 0  # nothing open: nothing sent
    assert main(["daily", "cancel", "vid_X"], env) == 3
    assert "not a daily video" in capsys.readouterr().err
