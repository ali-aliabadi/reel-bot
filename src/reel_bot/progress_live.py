"""Independent status-card updates: no writes to the video database or pipeline."""

import hashlib
import json
import logging
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from reel_bot import progress_view, relay_api, store_progress, videos, web

log = logging.getLogger(__name__)


class Messenger(Protocol):
    def send(self, message: dict[str, object]) -> str: ...
    def edit_text(self, message_id: str, text: str) -> None: ...
    def message_status(self, message_id: str) -> str: ...
    def delete_text(self, message_id: str) -> None: ...
    def supports_delete(self) -> bool: ...


@dataclass(frozen=True)
class Card:
    message_id: str
    terminal: str = ""
    completion_id: str = ""
    anchor: str = ""
    revision: int = 0
    retired: tuple[str, ...] = ()


def card_path(folder: Path) -> Path:
    return folder / "progress-card.json"


def read_card(folder: Path) -> Card | None:
    try:
        raw: object = json.loads(card_path(folder).read_text())
        if not isinstance(raw, dict):
            return None
        data = cast(dict[str, object], raw)
        revision = data.get("revision", 0)
        retired = data.get("retired", [])
        if (
            not isinstance(revision, int)
            or revision < 0
            or not isinstance(retired, list)
            or any(
                not isinstance(mid, str) or not mid.startswith("msg_")
                for mid in cast(list[object], retired)
            )
        ):
            return None
        if isinstance(data["message_id"], str) and data["message_id"].startswith("msg_"):
            return Card(
                data["message_id"],
                str(data.get("terminal", "")),
                str(data.get("completion_id", "")),
                str(data.get("anchor", "")),
                revision,
                tuple(cast(list[str], retired)),
            )
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        pass
    return None


def save_card(folder: Path, card: Card) -> None:
    path = card_path(folder)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(card)))
    tmp.replace(path)


def anchor(s: store_progress.State) -> str:
    """A new outgoing step or completed stage follows a human reply. Counters,
    timestamps and transient retries must not create a message every poll."""
    event = [s.latest_message, *sorted(s.stages)]
    return hashlib.sha256(json.dumps(event).encode()).hexdigest()[:24]


def retire(relay: Messenger, folder: Path, card: Card) -> Card:
    """Delete older cards only after the replacement actually reaches the chat.
    Save each successful cleanup so an outage/restart can resume the remainder."""
    if not card.retired or relay.message_status(card.message_id) != "delivered":
        return card
    for mid in card.retired:
        try:
            relay.delete_text(mid)
        except (relay_api.RelayError, web.WebError):
            log.warning("old progress card cleanup deferred")
            continue
        card = replace(card, retired=tuple(old for old in card.retired if old != mid))
        save_card(folder, card)
    return card


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
        event = anchor(s)
        folder = videos.video_dir(self.out_dir, video_id)
        card = read_card(folder)
        if card:
            card = retire(self.relay, folder, card)
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
        if (
            view.terminal
            and card is not None
            and card.terminal == view.stage
            and card.anchor == event
        ):
            return False
        text = progress_view.text(s.title, view, now)
        if card is None or card.anchor != event:
            # Relay delivers asynchronously: don't let a queued prompt land
            # below a status card sent too early. Keep the old card live meanwhile.
            ready = (
                not s.latest_message or self.relay.message_status(s.latest_message) == "delivered"
            )
            if card is not None:
                ready = ready and self.relay.supports_delete()
            if ready:
                self._move(folder, video_id, card, event, text)
                return True
            if card is None:
                return False
        self.relay.edit_text(card.message_id, text)
        save_card(
            folder,
            replace(
                card,
                terminal=view.stage if view.terminal else "",
                completion_id=s.sent_message if view.terminal else "",
            ),
        )
        return True

    def _move(self, folder: Path, video_id: str, card: Card | None, event: str, text: str) -> None:
        revision = card.revision + 1 if card else 0
        pending = folder / "progress-send.json"
        if pending.is_file():
            try:
                data = json.loads(pending.read_text())
                planned_revision, planned_event = data["revision"], data["anchor"]
            except (ValueError, KeyError, TypeError, RecursionError) as err:
                raise ValueError("invalid progress send journal") from err
            if not isinstance(planned_revision, int) or not isinstance(planned_event, str):
                raise ValueError("invalid progress send journal")
            if card is None or planned_revision > card.revision:
                revision, event = planned_revision, planned_event
        tmp = pending.with_suffix(".tmp")
        tmp.write_text(json.dumps({"revision": revision, "anchor": event}))
        tmp.replace(pending)
        key = f"{video_id}-progress" + (f"-{revision}" if revision else "")
        mid = self.relay.send(
            {
                "to": list(dict.fromkeys(self.people)),
                "urgency": "low",
                "title": "🎬 Video progress",
                "text": text,
                "idempotency_key": key,
            }
        )
        old = (*card.retired, card.message_id) if card else ()
        save_card(folder, Card(mid, anchor=event, revision=revision, retired=old))
        pending.unlink(missing_ok=True)


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
