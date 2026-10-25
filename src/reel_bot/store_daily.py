"""SQL for the daily run's Relay steps (docs/ARCHITECTURE.md, Daily run)."""

import sqlite3
from datetime import datetime

from reel_bot.store_videos import get_video


def add_step(
    conn: sqlite3.Connection, video_id: str, step: str, message_id: str, at: datetime
) -> None:
    """Record the Relay message sent for a step (replaces an earlier one)."""
    get_video(conn, video_id)
    conn.execute(
        "INSERT OR REPLACE INTO daily_steps (video_id, step, message_id, at) VALUES (?, ?, ?, ?)",
        (video_id, step, message_id, at.isoformat()),
    )


def clear_step(conn: sqlite3.Connection, video_id: str, step: str) -> None:
    conn.execute("DELETE FROM daily_steps WHERE video_id = ? AND step = ?", (video_id, step))


def steps(conn: sqlite3.Connection, video_id: str) -> dict[str, str]:
    """{step: Relay message ID} for a video."""
    rows = conn.execute(
        "SELECT step, message_id FROM daily_steps WHERE video_id = ?", (video_id,)
    ).fetchall()
    return {step: mid for step, mid in rows}


def open_videos(conn: sqlite3.Connection) -> list[str]:
    """Daily videos still in progress (asked about, not sent and not failed), oldest first."""
    rows = conn.execute(
        "SELECT video_id FROM daily_steps WHERE step = 'pick' AND video_id NOT IN"
        " (SELECT video_id FROM daily_steps WHERE step IN ('sent', 'failed'))"
        " ORDER BY video_id"
    ).fetchall()
    return [vid for (vid,) in rows]
