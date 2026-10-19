"""SQLite storage. The only module that writes SQL (docs/ARCHITECTURE.md, Data)."""

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from reel_bot.stories import Story

# Append-only: never edit a migration that has shipped; add a new one.
MIGRATIONS = (
    """
    CREATE TABLE stories (
        id TEXT PRIMARY KEY,
        tale TEXT NOT NULL,
        title TEXT NOT NULL,
        collection TEXT NOT NULL,
        source TEXT NOT NULL,
        source_ref TEXT NOT NULL
    );
    CREATE INDEX stories_tale ON stories (tale);
    -- One row per tale ever told. The primary key is the no-repeat rule.
    CREATE TABLE tales_used (
        tale TEXT PRIMARY KEY,
        story_id TEXT NOT NULL REFERENCES stories (id),
        video_id TEXT NOT NULL,
        used_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE videos (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        framing TEXT NOT NULL CHECK (framing IN ('fable', 'lesson')),
        length TEXT NOT NULL CHECK (length IN ('short', 'long')),
        story_options TEXT NOT NULL DEFAULT '[]',  -- JSON list of story IDs offered to ali
        story_id TEXT REFERENCES stories (id),
        chosen_by TEXT,
        chosen_at TEXT,
        -- The human pick is all or nothing.
        CHECK ((story_id IS NULL) = (chosen_by IS NULL)
            AND (story_id IS NULL) = (chosen_at IS NULL))
    );
    CREATE TABLE video_stages (
        video_id TEXT NOT NULL REFERENCES videos (id),
        stage TEXT NOT NULL,
        path TEXT NOT NULL,
        cost_usd REAL NOT NULL,
        seconds REAL NOT NULL,
        finished_at TEXT NOT NULL,
        PRIMARY KEY (video_id, stage)
    );
    """,
    """
    -- Where every picture, music, footage or font file came from, and its licence.
    CREATE TABLE assets (
        video_id TEXT NOT NULL REFERENCES videos (id),
        path TEXT NOT NULL,  -- relative to out/<video id>/
        kind TEXT NOT NULL CHECK (kind IN ('image', 'music', 'footage', 'font')),
        source TEXT NOT NULL,
        licence TEXT NOT NULL,
        added_at TEXT NOT NULL,
        PRIMARY KEY (video_id, path)
    );
    """,
    """
    -- One row per paid API call, written as soon as it returns: the monthly spend cap.
    CREATE TABLE spend (
        at TEXT NOT NULL,
        video_id TEXT NOT NULL REFERENCES videos (id),
        what TEXT NOT NULL,
        cost_usd REAL NOT NULL CHECK (cost_usd >= 0)
    );
    CREATE INDEX spend_at ON spend (at);
    """,
    """
    -- One row per platform a video went out on.
    CREATE TABLE posts (
        video_id TEXT NOT NULL REFERENCES videos (id),
        platform TEXT NOT NULL CHECK (platform IN ('youtube', 'instagram', 'tiktok')),
        platform_id TEXT NOT NULL,
        posted_at TEXT NOT NULL,
        PRIMARY KEY (video_id, platform)
    );
    -- The human pick rule, in the database too: no post without a recorded pick.
    CREATE TRIGGER posts_need_a_pick BEFORE INSERT ON posts
    WHEN (SELECT chosen_at FROM videos WHERE id = NEW.video_id) IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'no recorded story pick for this video');
    END;
    """,
)


class StoreError(Exception):
    pass


class UnknownStoryError(StoreError):
    def __init__(self, story_id: str) -> None:
        super().__init__(f"no story {story_id!r} in the catalog")


class TaleAlreadyUsedError(StoreError):
    def __init__(self, tale: str, video_id: str) -> None:
        self.tale = tale
        self.video_id = video_id
        super().__init__(f"tale {tale!r} was already told in video {video_id}")


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database and bring its schema up to date."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for n, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {n}; COMMIT;")


def sync_stories(conn: sqlite3.Connection, stories: Iterable[Story]) -> None:
    """Insert new catalog entries and update changed ones; never deletes."""
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO stories (id, tale, title, collection, source, source_ref)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (id) DO UPDATE SET tale = excluded.tale, title = excluded.title,"
        " collection = excluded.collection, source = excluded.source,"
        " source_ref = excluded.source_ref",
        [(s.id, s.tale, s.title, s.collection, s.source, s.source_ref) for s in stories],
    )
    conn.execute("COMMIT")


def all_stories(conn: sqlite3.Connection) -> list[Story]:
    rows = conn.execute(
        "SELECT id, tale, title, collection, source, source_ref FROM stories ORDER BY id"
    ).fetchall()
    return [Story(*row) for row in rows]


def used_tales(conn: sqlite3.Connection) -> set[str]:
    return {tale for (tale,) in conn.execute("SELECT tale FROM tales_used")}


def mark_used(conn: sqlite3.Connection, story_id: str, video_id: str, now: datetime) -> None:
    """Record that a video tells this story; refuses a tale that was already told."""
    row = conn.execute("SELECT tale FROM stories WHERE id = ?", (story_id,)).fetchone()
    if row is None:
        raise UnknownStoryError(story_id)
    (tale,) = row
    try:
        conn.execute(
            "INSERT INTO tales_used (tale, story_id, video_id, used_at) VALUES (?, ?, ?, ?)",
            (tale, story_id, video_id, now.isoformat()),
        )
    except sqlite3.IntegrityError as err:
        (earlier,) = conn.execute(
            "SELECT video_id FROM tales_used WHERE tale = ?", (tale,)
        ).fetchone()
        raise TaleAlreadyUsedError(tale, earlier) from err
