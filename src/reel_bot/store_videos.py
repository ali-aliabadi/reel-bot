"""SQL for videos and their stage runs (docs/ARCHITECTURE.md, Data)."""

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from reel_bot.store import StoreError, mark_used
from reel_bot.videos import Asset, StageRun, Video


class UnknownVideoError(StoreError):
    def __init__(self, video_id: str) -> None:
        super().__init__(f"no video {video_id!r}")


class PickError(StoreError):
    pass


def add_video(conn: sqlite3.Connection, video: Video) -> None:
    conn.execute(
        "INSERT INTO videos (id, created_at, framing, length, story_options)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            video.id,
            video.created_at.isoformat(),
            video.framing,
            video.length,
            json.dumps(list(video.story_options)),
        ),
    )


def _video(row: tuple[str, str, str, str, str, str | None, str | None, str | None]) -> Video:
    vid, created, framing, length, options, story_id, chosen_by, chosen_at = row
    return Video(
        id=vid,
        created_at=datetime.fromisoformat(created),
        framing=framing,
        length=length,
        story_options=tuple(json.loads(options)),
        story_id=story_id,
        chosen_by=chosen_by,
        chosen_at=datetime.fromisoformat(chosen_at) if chosen_at else None,
    )


def get_video(conn: sqlite3.Connection, video_id: str) -> Video:
    row = conn.execute(
        "SELECT id, created_at, framing, length, story_options, story_id, chosen_by, chosen_at"
        " FROM videos WHERE id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        raise UnknownVideoError(video_id)
    return _video(row)


def set_options(conn: sqlite3.Connection, video_id: str, story_ids: list[str]) -> None:
    """Record the stories offered to ali for this video."""
    cur = conn.execute(
        "UPDATE videos SET story_options = ? WHERE id = ? AND story_id IS NULL",
        (json.dumps(story_ids), video_id),
    )
    if cur.rowcount == 0:
        get_video(conn, video_id)  # raises for an unknown video
        raise PickError(f"video {video_id} already has its story")


def choose_story(
    conn: sqlite3.Connection, video_id: str, story_id: str, chosen_by: str, now: datetime
) -> None:
    """Record the human pick and spend the tale, together or not at all.

    When options were offered, the pick must be one of them.
    """
    video = get_video(conn, video_id)
    if video.story_id is not None:
        raise PickError(f"video {video_id} already has its story")
    if video.story_options and story_id not in video.story_options:
        raise PickError(f"story {story_id!r} was not one of the options for {video_id}")
    if not chosen_by.strip():
        raise PickError("a pick needs who made it")
    conn.execute("BEGIN")
    try:
        mark_used(conn, story_id, video_id, now)
        conn.execute(
            "UPDATE videos SET story_id = ?, chosen_by = ?, chosen_at = ? WHERE id = ?",
            (story_id, chosen_by, now.isoformat(), video_id),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def record_stage(conn: sqlite3.Connection, video_id: str, run: StageRun) -> None:
    """Store (or replace) a stage's output for a video."""
    get_video(conn, video_id)
    conn.execute(
        "INSERT OR REPLACE INTO video_stages"
        " (video_id, stage, path, cost_usd, seconds, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
        (video_id, run.stage, run.path, run.cost_usd, run.seconds, run.finished_at.isoformat()),
    )


def clear_stages(conn: sqlite3.Connection, video_id: str, stages: Sequence[str]) -> None:
    """Forget stages so the next run redoes them (their old files are overwritten)."""
    conn.executemany(
        "DELETE FROM video_stages WHERE video_id = ? AND stage = ?",
        [(video_id, stage) for stage in stages],
    )


def stage_runs(conn: sqlite3.Connection, video_id: str) -> dict[str, StageRun]:
    rows = conn.execute(
        "SELECT stage, path, cost_usd, seconds, finished_at FROM video_stages WHERE video_id = ?",
        (video_id,),
    ).fetchall()
    return {r[0]: StageRun(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4])) for r in rows}


def add_asset(conn: sqlite3.Connection, video_id: str, asset: Asset) -> None:
    """Record (or replace) where a video's file came from."""
    get_video(conn, video_id)
    conn.execute(
        "INSERT OR REPLACE INTO assets (video_id, path, kind, source, licence, added_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (video_id, asset.path, asset.kind, asset.source, asset.licence, asset.added_at.isoformat()),
    )


def assets(conn: sqlite3.Connection, video_id: str) -> list[Asset]:
    rows = conn.execute(
        "SELECT path, kind, source, licence, added_at FROM assets WHERE video_id = ? ORDER BY path",
        (video_id,),
    ).fetchall()
    return [Asset(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4])) for r in rows]


def add_spend(
    conn: sqlite3.Connection, video_id: str, what: str, cost_usd: float, at: datetime
) -> None:
    conn.execute(
        "INSERT INTO spend (at, video_id, what, cost_usd) VALUES (?, ?, ?, ?)",
        (at.isoformat(), video_id, what, cost_usd),
    )


def spent_since(conn: sqlite3.Connection, since: datetime) -> float:
    """US dollars of paid calls at or after `since` (UTC ISO strings sort by time)."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE at >= ?", (since.isoformat(),)
    ).fetchone()
    return float(total)


def add_post(
    conn: sqlite3.Connection, video_id: str, platform: str, platform_id: str, at: datetime
) -> None:
    """Record that a video went out on a platform (refused without a recorded pick)."""
    get_video(conn, video_id)
    try:
        conn.execute(
            "INSERT INTO posts (video_id, platform, platform_id, posted_at) VALUES (?, ?, ?, ?)",
            (video_id, platform, platform_id, at.isoformat()),
        )
    except sqlite3.IntegrityError as err:
        raise PickError(f"video {video_id} can't be recorded as posted: {err}") from err


def posts(conn: sqlite3.Connection, video_id: str) -> dict[str, str]:
    """{platform: platform's ID for the post} for a video."""
    rows = conn.execute(
        "SELECT platform, platform_id FROM posts WHERE video_id = ?", (video_id,)
    ).fetchall()
    return {platform: pid for platform, pid in rows}
