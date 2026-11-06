"""Independent status-card updates: no writes to the video database or pipeline."""

import json
import logging
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from reel_bot import progress_view, store_progress, videos

log = logging.getLogger(__name__)


class Messenger(Protocol):
    def send(self, message: dict[str, object]) -> str: ...
    def edit_text(self, message_id: str, text: str) -> None: ...
    def message_status(self, message_id: str) -> str: ...


@dataclass(frozen=True)
class Card:
    message_id: str
    terminal: str = ""
    completion_id: str = ""


def card_path(folder: Path) -> Path:
    return folder / "progress-card.json"


def read_card(folder: Path) -> Card | None:
    try:
        data = json.loads(card_path(folder).read_text())
        if isinstance(data["message_id"], str) and data["message_id"].startswith("msg_"):
            return Card(
                data["message_id"],
                str(data.get("terminal", "")),
                str(data.get("completion_id", "")),
            )
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        pass
    return None


def save_card(folder: Path, card: Card) -> None:
    path = card_path(folder)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(card)))
    tmp.replace(path)


def worker_active() -> bool:
    """A oneshot worker is 'activating' for its entire run, not 'active'."""
    try:
        done = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "reel-bot-daily-tick.service",
                "--property=ActiveState",
                "--value",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return done.returncode == 0 and done.stdout.strip() in ("activating", "active", "reloading")
    except (OSError, subprocess.TimeoutExpired):
        return False


@dataclass(frozen=True)
class Observer:
    relay: Messenger
    out_dir: Path
    people: tuple[str, ...]
    narration: str
    active: Callable[[], bool] = worker_active

    def update(
        self, conn: sqlite3.Connection, video_id: str, now: datetime, force: bool = False
    ) -> bool:
        s = store_progress.state(conn, video_id)
        folder = videos.video_dir(self.out_dir, video_id)
        card = read_card(folder)
        closed = s.steps.intersection(("sent", "failed", "cancelled"))
        if closed and card is None and not force:
            return False  # Don't send a card for historical completed cycles.
        if card and card.terminal and s.sent_message and card.completion_id == s.sent_message:
            return False
        view = progress_view.build(
            s, folder, (self.active() if not closed else False), self.narration
        )
        if s.sent_message:
            view = delivered_view(view, self.relay.message_status(s.sent_message))
        if view.terminal and card is not None and card.terminal == view.stage:
            return False
        text = progress_view.text(s.title, view, now)
        if card is None:
            mid = self.relay.send(
                {
                    "to": list(dict.fromkeys(self.people)),
                    "urgency": "low",
                    "title": "🎬 Video progress",
                    "text": text,
                    "idempotency_key": f"{video_id}-progress",
                }
            )
            card = Card(mid)
            save_card(folder, card)
            # The async initial send may still be queued; edit on the next pass.
            return True
        self.relay.edit_text(card.message_id, text)
        save_card(
            folder,
            Card(
                card.message_id,
                view.stage if view.terminal else "",
                s.sent_message if view.terminal else "",
            ),
        )
        return True


def delivered_view(view: progress_view.View, status: str) -> progress_view.View:
    if status == "delivered":
        return view
    if status in ("failed", "partially_delivered"):
        return progress_view.View(
            "⚠️ Video delivery failed",
            "Video is ready, but Telegram delivery needs attention",
            terminal=True,
        )
    return progress_view.View(
        "Delivering the video", "Render complete; waiting for delivery to finish"
    )
