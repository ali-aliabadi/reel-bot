"""Cancelling a daily video (see `daily`): ali taps "Skip today" on the pick,
a pick nobody answered is replaced the next morning, or `reel-bot daily cancel`.

A cancelled video is closed for good: ticks pass it by, its tale goes back to
the catalog (the pick stays on the video row as the record), and if Tara had
already been asked for anything she gets one note saying she can ignore it.
"""

import logging
from datetime import timedelta

from reel_bot import store, store_daily, store_videos
from reel_bot.daily_run import DailyError, Run

log = logging.getLogger(__name__)

# A pick still unanswered this long after it was asked is replaced by the next
# morning's (the start timer runs once a day, so under 24 hours).
EXPIRE_AFTER = timedelta(hours=20)
_NOT_TARA = {"pick", "failed", "cancelled"}  # steps that never asked Tara anything


def cancel_message(to: str, video_id: str, title: str) -> dict[str, object]:
    return {
        "to": [to],
        "urgency": "low",
        "title": f"Cancelled: {title}"[:256] if title else "Today's fable is cancelled",
        "idempotency_key": f"{video_id}-cancelled",
        "blocks": [
            {
                "type": "text",
                "text": "This video is cancelled. You can ignore its questions; "
                "nothing more will come for it.",
            }
        ],
    }


def cancel(run: Run, video_id: str, why: str) -> None:
    """Close a daily video that hasn't been sent yet (open or failed)."""
    conn = run.conn
    sent = store_daily.steps(conn, video_id)
    if "pick" not in sent:
        raise DailyError(f"{video_id} is not a daily video")
    if "sent" in sent:
        raise DailyError(f"{video_id} was already sent to Tara")
    if "cancelled" in sent:
        raise DailyError(f"{video_id} is already cancelled")
    mid = ""
    if set(sent) - _NOT_TARA:
        story_id = store_videos.get_video(conn, video_id).story_id
        title = next((s.title for s in store.all_stories(conn) if s.id == story_id), "")
        mid = run.relay.send(cancel_message(run.people.narrator, video_id, title))
    store_daily.cancel(conn, video_id, mid, run.clock())
    log.info("daily video cancelled", extra={"video_id": video_id, "detail": why})


def expire_picks(run: Run) -> list[str]:
    """Cancel picks left unanswered for EXPIRE_AFTER; returns their video IDs."""
    expired: list[str] = []
    now = run.clock()
    for vid in store_daily.open_videos(run.conn):
        if store_videos.get_video(run.conn, vid).story_id is not None:
            continue
        asked = store_daily.asked_at(run.conn, vid, "pick")
        if asked is not None and now - asked >= EXPIRE_AFTER:
            cancel(run, vid, "no pick by the next morning")
            expired.append(vid)
    return expired
