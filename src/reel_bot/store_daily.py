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


def reask(
    conn: sqlite3.Connection, video_id: str, step: str, message_id: str, at: datetime
) -> None:
    """Record that a step's question was sent again as a new message."""
    conn.execute(
        "UPDATE daily_steps SET message_id = ?, at = ?, asks = asks + 1"
        " WHERE video_id = ? AND step = ?",
        (message_id, at.isoformat(), video_id, step),
    )


def asks(conn: sqlite3.Connection, video_id: str, step: str) -> int:
    """How many times a step's question was sent; 0 when never."""
    row = conn.execute(
        "SELECT asks FROM daily_steps WHERE video_id = ? AND step = ?", (video_id, step)
    ).fetchone()
    return int(row[0]) if row else 0


def add_error(conn: sqlite3.Connection, video_id: str, at: datetime) -> int:
    """Count one more error in a row for a video; returns the count."""
    conn.execute(
        "INSERT INTO daily_errors (video_id, count, at) VALUES (?, 1, ?)"
        " ON CONFLICT (video_id) DO UPDATE SET count = count + 1, at = excluded.at",
        (video_id, at.isoformat()),
    )
    row = conn.execute("SELECT count FROM daily_errors WHERE video_id = ?", (video_id,)).fetchone()
    return int(row[0])


def clear_errors(conn: sqlite3.Connection, video_id: str) -> None:
    conn.execute("DELETE FROM daily_errors WHERE video_id = ?", (video_id,))


def begin(conn: sqlite3.Connection, video_id: str, at: datetime) -> int:
    """Count a tick starting on a video; returns how many started without ending."""
    conn.execute(
        "INSERT INTO daily_running (video_id, count, at) VALUES (?, 1, ?)"
        " ON CONFLICT (video_id) DO UPDATE SET count = count + 1, at = excluded.at",
        (video_id, at.isoformat()),
    )
    row = conn.execute("SELECT count FROM daily_running WHERE video_id = ?", (video_id,)).fetchone()
    return int(row[0])


def end(conn: sqlite3.Connection, video_id: str) -> None:
    """A tick on a video finished (well or with an error it caught)."""
    conn.execute("DELETE FROM daily_running WHERE video_id = ?", (video_id,))


def clear_step(conn: sqlite3.Connection, video_id: str, step: str) -> None:
    conn.execute("DELETE FROM daily_steps WHERE video_id = ? AND step = ?", (video_id, step))


def steps(conn: sqlite3.Connection, video_id: str) -> dict[str, str]:
    """{step: Relay message ID} for a video."""
    rows = conn.execute(
        "SELECT step, message_id FROM daily_steps WHERE video_id = ?", (video_id,)
    ).fetchall()
    return {step: mid for step, mid in rows}


def open_videos(conn: sqlite3.Connection) -> list[str]:
    """Daily videos still in progress (asked about, not sent, failed or cancelled), oldest first."""
    rows = conn.execute(
        "SELECT video_id FROM daily_steps WHERE step = 'pick' AND video_id NOT IN"
        " (SELECT video_id FROM daily_steps WHERE step IN ('sent', 'failed', 'cancelled'))"
        " ORDER BY video_id"
    ).fetchall()
    return [vid for (vid,) in rows]


def asked_at(conn: sqlite3.Connection, video_id: str, step: str) -> datetime | None:
    """When a step's question was last sent; None when never."""
    row = conn.execute(
        "SELECT at FROM daily_steps WHERE video_id = ? AND step = ?", (video_id, step)
    ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def unfinished(conn: sqlite3.Connection) -> list[str]:
    """Daily videos neither sent nor cancelled (open or failed), oldest first."""
    rows = conn.execute(
        "SELECT video_id FROM daily_steps WHERE step = 'pick' AND video_id NOT IN"
        " (SELECT video_id FROM daily_steps WHERE step IN ('sent', 'cancelled'))"
        " ORDER BY video_id"
    ).fetchall()
    return [vid for (vid,) in rows]


def cancel(conn: sqlite3.Connection, video_id: str, message_id: str, at: datetime) -> None:
    """Close a daily video for good and give its tale back, together or not at all.
    The pick stays on the video row as the record of what was chosen."""
    conn.execute("BEGIN")
    try:
        add_step(conn, video_id, "cancelled", message_id, at)
        conn.execute("DELETE FROM tales_used WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM daily_errors WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM daily_running WHERE video_id = ?", (video_id,))
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
