import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reel_bot import budget, config, store, store_videos, videos
from reel_bot.script_write import Reply
from reel_bot.videos import Video
from reel_bot.visuals_write import Drawn

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return store.open_db(tmp_path / "reel-bot.db")


def video(conn: sqlite3.Connection) -> str:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    return v.id


def test_counts_only_this_month(conn: sqlite3.Connection) -> None:
    vid = video(conn)
    store_videos.add_spend(conn, vid, "picture", 9.0, datetime(2026, 10, 31, 23, 59, tzinfo=UTC))
    store_videos.add_spend(conn, vid, "picture", 1.5, datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    b = budget.Budget(conn, vid, 2.0, lambda: NOW)
    assert b.spent() == 1.5
    b.allow("a picture", 0.5)
    with pytest.raises(budget.BudgetError, match=r"\$1.50 of this month's \$2.00"):
        b.allow("a picture", 0.51)


def test_wrappers_refuse_before_calling_and_record_after(conn: sqlite3.Connection) -> None:
    vid = video(conn)
    b = budget.Budget(conn, vid, 0.4, lambda: NOW)
    calls: list[str] = []

    def claude(system: str, user: str, schema: dict[str, Any]) -> Reply:
        calls.append("claude")
        return Reply("{}", "claude-opus-5-5", 10_000, 5_000)  # $0.14

    def draw(prompt: str) -> Drawn:
        calls.append("draw")
        return Drawn(b"png", 0.063)

    b.claude(claude)("s", "u", {})
    b.draw(draw)("p")
    assert calls == ["claude", "draw"]
    assert b.spent() == pytest.approx(0.14 + 0.063)
    with pytest.raises(budget.BudgetError, match="a script"):
        b.claude(claude)("s", "u", {})  # $0.20 spent + $0.35 worst case > $0.40
    assert calls == ["claude", "draw"]


@pytest.mark.parametrize(
    ("raw", "cap"),
    [("", 20.0), ("0", 0.0), (" 7.5 ", 7.5), ("x", None), ("-1", None), ("1e9", None)],
)
def test_monthly_cap_from_env(raw: str, cap: float | None) -> None:
    env = {"REEL_BOT_MONTHLY_CAP_USD": raw}
    if cap is None:
        with pytest.raises(config.ConfigError, match="REEL_BOT_MONTHLY_CAP_USD"):
            config.monthly_cap(env)
    else:
        assert config.monthly_cap(env) == cap
