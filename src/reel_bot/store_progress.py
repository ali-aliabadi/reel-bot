"""Read-only database access for the independent bot progress observer."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class State:
    video_id: str
    title: str
    picked: bool
    steps: frozenset[str]
    stages: frozenset[str]
    errors: int
    running_at: datetime | None
    sent_message: str = ""


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"file:{quote(str(path.resolve()))}?mode=ro", uri=True, isolation_level=None
    )


def recent(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT video_id FROM daily_steps WHERE step='pick' ORDER BY video_id DESC LIMIT 50"
        )
    ]


def state(conn: sqlite3.Connection, video_id: str) -> State:
    row = conn.execute(
        "SELECT v.story_id, coalesce(s.title, 'New story') FROM videos v "
        "LEFT JOIN stories s ON s.id=v.story_id WHERE v.id=?",
        (video_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown video")
    messages = dict(
        conn.execute("SELECT step,message_id FROM daily_steps WHERE video_id=?", (video_id,))
    )
    steps = frozenset(messages)
    stages = frozenset(
        r[0] for r in conn.execute("SELECT stage FROM video_stages WHERE video_id=?", (video_id,))
    )
    error = conn.execute("SELECT count FROM daily_errors WHERE video_id=?", (video_id,)).fetchone()
    running = conn.execute("SELECT at FROM daily_running WHERE video_id=?", (video_id,)).fetchone()
    return State(
        video_id,
        row[1],
        row[0] is not None,
        steps,
        stages,
        error[0] if error else 0,
        datetime.fromisoformat(running[0]) if running else None,
        messages.get("sent", ""),
    )
