"""SQL for the Relay messages reel-bot waits on (docs/ARCHITECTURE.md, Data)."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RelayMessage:
    message_id: str
    video_id: str
    purpose: str  # what the answer is for, e.g. "pick"
    sent_at: datetime


def add_message(conn: sqlite3.Connection, m: RelayMessage) -> None:
    conn.execute(
        "INSERT INTO relay_messages (message_id, video_id, purpose, sent_at) VALUES (?, ?, ?, ?)",
        (m.message_id, m.video_id, m.purpose, m.sent_at.isoformat()),
    )


def open_messages(conn: sqlite3.Connection, purpose: str) -> list[RelayMessage]:
    """Messages still waiting for an answer, oldest first."""
    rows = conn.execute(
        "SELECT message_id, video_id, purpose, sent_at FROM relay_messages"
        " WHERE purpose = ? AND closed_at IS NULL ORDER BY sent_at",
        (purpose,),
    ).fetchall()
    return [RelayMessage(m, v, p, datetime.fromisoformat(at)) for m, v, p, at in rows]


def close_message(conn: sqlite3.Connection, message_id: str, outcome: str, at: datetime) -> None:
    conn.execute(
        "UPDATE relay_messages SET closed_at = ?, outcome = ? WHERE message_id = ?",
        (at.isoformat(), outcome, message_id),
    )


def last_sent(conn: sqlite3.Connection, purpose: str) -> RelayMessage | None:
    row = conn.execute(
        "SELECT message_id, video_id, purpose, sent_at FROM relay_messages"
        " WHERE purpose = ? ORDER BY sent_at DESC LIMIT 1",
        (purpose,),
    ).fetchone()
    return (
        None
        if row is None
        else RelayMessage(row[0], row[1], row[2], datetime.fromisoformat(row[3]))
    )


def unasked_video(conn: sqlite3.Connection, purpose: str) -> str | None:
    """The newest video with story options, no story and no message for `purpose`:
    one a run created but stopped before recording what it sent."""
    row = conn.execute(
        "SELECT id FROM videos v WHERE story_id IS NULL AND story_options != '[]'"
        " AND NOT EXISTS (SELECT 1 FROM relay_messages r"
        " WHERE r.video_id = v.id AND r.purpose = ?)"
        " ORDER BY created_at DESC LIMIT 1",
        (purpose,),
    ).fetchone()
    return None if row is None else str(row[0])


def picked_count(conn: sqlite3.Connection) -> int:
    (n,) = conn.execute("SELECT count(*) FROM videos WHERE story_id IS NOT NULL").fetchone()
    return int(n)
