"""The morning pick, pure core: the Relay question offering ali three stories, and
reading the button ali tapped.

`morning_write` sends the question, reads the answer and records the pick. The
experiment settings rotate with the number of picked videos, so a skipped day
doesn't shift them: fable/short, lesson/short, fable/long, lesson/long, again.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from reel_bot import relay_api, videos
from reel_bot.stories import Story

PURPOSE = "pick"
CHOICES = 3
SKIP = "Skip today"
EXPIRE_AFTER = timedelta(hours=20)  # an unanswered question gives way to the next morning's
MAX_OPTION = 64  # Relay's limit for a button


class MorningError(Exception):
    pass


@dataclass(frozen=True)
class Decision:
    outcome: str  # picked, skipped or unreadable
    story_id: str | None = None
    by: str | None = None


def settings(picked: int) -> tuple[str, str]:
    """(framing, length) for the next video, given how many videos have a pick."""
    framing = videos.FRAMINGS[picked % len(videos.FRAMINGS)]
    length = videos.LENGTHS[(picked // len(videos.FRAMINGS)) % len(videos.LENGTHS)]
    return framing, length


def labels(options: Sequence[Story]) -> list[str]:
    """One button per story, numbered so two stories with the same title stay apart."""
    return [f"{n}. {s.title}"[:MAX_OPTION] for n, s in enumerate(options, start=1)]


def question(to: str, video: videos.Video, options: Sequence[Story]) -> dict[str, object]:
    """The Relay message body (source and idempotency key are added by the client)."""
    lines = [f"{label} ({s.collection})" for label, s in zip(labels(options), options, strict=True)]
    return {
        "to": [to],
        "urgency": "normal",
        "title": "Pick today's fable",
        "blocks": [
            {"type": "text", "text": "\n".join(lines)},
            {"type": "fields", "items": [
                {"label": "Framing", "value": video.framing},
                {"label": "Length", "value": video.length},
            ]},
            {
                "type": "question",
                "text": "Which fable should Tara tell next?",
                "options": [*labels(options), SKIP],
            },
        ],
    }  # fmt: skip


def decide(answers: Sequence[relay_api.Answer], options: Sequence[Story]) -> Decision | None:
    """The first answer as a decision, or None while nobody has answered.

    The answer is compared with our own button texts; anything else (a typed
    reply) is untrusted and only marks the question unreadable.
    """
    if not answers:
        return None
    first = min(answers, key=lambda a: a.answered_at)
    if first.answer == SKIP:
        return Decision("skipped", by=first.recipient)
    for label, story in zip(labels(options), options, strict=True):
        if first.answer == label:
            return Decision("picked", story.id, first.recipient)
    return Decision("unreadable", by=first.recipient)
