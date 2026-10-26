"""Relay: the messages reel-bot sends to ali and Tara, and their answers.

The message builders and the answer parser are pure; `Relay` sends them with
`web.Net` (timeout and bounded retries). Every message carries an idempotency
key made from the video and the step, so a retry or a rerun never sends twice.
Message bodies, answers, audio and pictures are never logged, only IDs.
"""

import base64
import binascii
import json
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from reel_bot import web
from reel_bot.stories import Story

MAX_FILE_BYTES = 45 << 20  # under Telegram's 50 MB bot upload; Relay must allow it too
MAX_OPTION = 64  # Relay's limit on one button's text
MAX_TEXT = 4000  # Relay's limit on one text block
_AUDIO_EXT = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a"}


class RelayError(Exception):
    pass


@dataclass(frozen=True)
class Answer:
    recipient: str  # Relay username of who answered
    text: str  # the button or typed reply; "" for a voice note or picture
    audio: bytes | None  # a voice note or audio file
    audio_type: str
    image: bytes | None = None  # a picture (Relay keeps only PNG, JPEG and WebP)

    def audio_suffix(self) -> str:
        return _AUDIO_EXT.get(self.audio_type, ".audio")


def options(stories: list[Story]) -> list[str]:
    """One unique button label per story, in order, each within Relay's limit."""
    labels: list[str] = []
    for n, s in enumerate(stories, start=1):
        label = f"{n}. {s.title}"
        labels.append(label if len(label) <= MAX_OPTION else label[: MAX_OPTION - 1] + "…")
    return labels


def pick_message(to: str, video_id: str, stories: list[Story]) -> dict[str, object]:
    lines = [f"{n}. {s.title} ({s.collection})" for n, s in enumerate(stories, start=1)]
    return {
        "to": [to],
        "urgency": "normal",
        "title": "Today's fable",
        "idempotency_key": f"{video_id}-pick",
        "blocks": [
            {"type": "text", "text": "\n".join(lines)},
            {"type": "question", "text": "Which story today?", "options": options(stories)},
        ],
    }


def take_message(to: str, video_id: str, title: str, spoken: str) -> dict[str, object]:
    if len(spoken) > MAX_TEXT:
        raise RelayError(f"the script is {len(spoken)} characters, over Relay's {MAX_TEXT}")
    return {
        "to": [to],
        "urgency": "normal",
        "title": f"Please read: {title}"[:256],
        "idempotency_key": f"{video_id}-take",
        "blocks": [
            {"type": "text", "text": spoken},
            {
                "type": "question",
                "text": "Reply with a voice note of you reading it (or an audio file).",
                "reply": "voice",
            },
        ],
    }


def script_message(
    to: str, video_id: str, title: str, paste: str, attempt: int
) -> dict[str, object]:
    """Asks Tara to paste `paste` into ChatGPT and reply with its answer; from the
    second attempt on, `paste` asks the same chat to fix its script."""
    if len(paste) > MAX_TEXT:
        raise RelayError(f"the script prompt is over Relay's {MAX_TEXT} characters")
    first = attempt == 1
    return {
        "to": [to],
        "urgency": "normal",
        "title": (f"Script: {title}" if first else f"Script, try {attempt}: {title}")[:256],
        "idempotency_key": f"{video_id}-script-{attempt}",
        "blocks": [
            {
                "type": "text",
                "text": "Copy this into a new ChatGPT chat:"
                if first
                else "ChatGPT's script needs a fix. Copy this into the same chat:",
            },
            {"type": "code", "text": paste},
            {"type": "question", "text": "Reply to this message with ChatGPT's whole answer."},
        ],
    }


def picture_message(
    to: str, video_id: str, title: str, prompts: Sequence[str], number: int
) -> dict[str, object]:
    """Asks Tara to make picture `number` (from 1) from its prompt and send it back."""
    prompt, count = prompts[number - 1], len(prompts)
    if len(prompt) > MAX_TEXT:
        raise RelayError(f"picture {number}'s prompt is over Relay's {MAX_TEXT} characters")
    return {
        "to": [to],
        "urgency": "normal",
        "title": f"Picture {number} of {count}: {title}"[:256],
        "idempotency_key": f"{video_id}-picture-{number}",
        "blocks": [
            {"type": "text", "text": prompt},
            {
                "type": "question",
                "text": "Make this picture in ChatGPT and reply to this message with it "
                "(send it as a file to keep full quality).",
                "reply": "image",
            },
        ],
    }


def again(message: dict[str, object], attempt: int, problem: str) -> dict[str, object]:
    """The same question sent again (attempt 2, 3, …) because its answer couldn't be
    used: Relay locks an answer once read, so a new answer needs a new message."""
    blocks = cast(list[object], message["blocks"])
    note = f"That didn't work: {problem}. Please send it again."
    return {
        **message,
        "title": f"Again: {message['title']}"[:256],
        "idempotency_key": f"{message['idempotency_key']}-{attempt}",
        "blocks": [{"type": "text", "text": note[:MAX_TEXT]}, *blocks],
    }


def file_message(
    to: list[str],
    video_id: str,
    title: str,
    path: Path,
    copy: Sequence[tuple[str, str]] = (),
) -> dict[str, object]:
    """The finished video, then each (label, text) in `copy` as a block to copy."""
    size = path.stat().st_size
    if not 0 < size <= MAX_FILE_BYTES:
        raise RelayError(f"the video is {size >> 20} MB; Relay can send 1 byte to 45 MB")
    return {
        "to": list(dict.fromkeys(to)),
        "urgency": "low",
        "title": f"Ready to upload: {title}"[:256],
        "idempotency_key": f"{video_id}-sent",
        "blocks": [
            {"type": "text", "text": "Your video is ready for YouTube and Instagram."},
            {
                "type": "file",
                "filename": f"{video_id}.mp4",
                "content_type": mimetypes.guess_type("x.mp4")[0] or "video/mp4",
                "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                "caption": title[:1024],
            },
            *(
                block
                for label, text in copy
                for block in (
                    {"type": "text", "text": label},
                    {"type": "code", "text": text[:MAX_TEXT]},
                )
            ),
        ],
    }


def alert_message(to: str, video_id: str, what: str, error: str, at: str) -> dict[str, object]:
    """A failure alert; `at` keeps a later failure of the same video from being deduplicated."""
    return {
        "to": [to],
        "urgency": "high",
        "title": "reel-bot stopped",
        "idempotency_key": f"{video_id}-failed-{at}"[:128],
        "blocks": [
            {
                "type": "fields",
                "items": [
                    {"label": "Video", "value": video_id},
                    {"label": "Step", "value": what},
                ],
            },
            {"type": "code", "text": error[:MAX_TEXT]},
            {"type": "text", "text": f"Fix it, then: reel-bot daily retry {video_id}"},
        ],
    }


def _file(a: dict[str, object], name: str) -> tuple[bytes | None, str]:
    """An answer's audio or image as (bytes, content type); (None, "") when it has none."""
    item = a.get(name)
    if item is None:
        return None, ""
    if not isinstance(item, dict):
        raise RelayError(f"an answer's {name} is not an object")
    f = cast(dict[str, object], item)
    kind, data = f.get("content_type"), f.get("base64")
    if not isinstance(kind, str) or not isinstance(data, str) or not data:
        raise RelayError(f"an answer's {name} has no content")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as err:
        raise RelayError(f"an answer's {name} is not base64") from err
    return raw, kind.split(";")[0].strip().lower()


def _answer(item: object) -> Answer:
    if not isinstance(item, dict):
        raise RelayError("an answer is not an object")
    a = cast(dict[str, object], item)
    who, text = a.get("recipient"), a.get("answer", "")
    if not isinstance(who, str) or not who or not isinstance(text, str):
        raise RelayError("an answer has no recipient or text")
    audio, audio_type = _file(a, "audio")
    image, _ = _file(a, "image")  # the bytes decide the type (visuals.suffix)
    return Answer(who, text, audio, audio_type, image)


def parse_answers(body: bytes) -> list[Answer]:
    """GET /v1/messages/{id}/answers as Answers; anything else raises RelayError."""
    try:
        data: object = json.loads(body)
    except (ValueError, RecursionError) as err:
        raise RelayError("Relay's answers are not JSON") from err
    items = cast(dict[str, object], data).get("answers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RelayError("Relay's reply has no answers list")
    return [_answer(i) for i in cast(list[object], items)]


def parse_sent(body: bytes) -> str:
    try:
        data: object = json.loads(body)
    except (ValueError, RecursionError) as err:
        raise RelayError("Relay's reply is not JSON") from err
    mid = cast(dict[str, object], data).get("id") if isinstance(data, dict) else None
    if not isinstance(mid, str) or not mid.startswith("msg_") or len(mid) > 64:  # noqa: PLR2004
        raise RelayError("Relay's reply has no message ID")
    return mid


@dataclass(frozen=True)
class Relay:
    url: str  # https base URL
    key: str
    app: str
    net: web.Net

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def send(self, message: dict[str, object]) -> str:
        """POST a message (with this app as its source); returns Relay's message ID."""
        body = json.dumps({**message, "source": self.app}).encode()
        req = web.Request("POST", f"{self.url}/v1/messages", "relay send", body, self._headers())
        return parse_sent(self.net.send(req).body)

    def answers(self, message_id: str) -> list[Answer]:
        if not message_id.startswith("msg_") or "/" in message_id:
            raise RelayError(f"not a Relay message ID: {message_id!r}")
        url = f"{self.url}/v1/messages/{message_id}/answers"
        req = web.Request("GET", url, "relay answers", b"", self._headers())
        return parse_answers(self.net.send(req).body)
