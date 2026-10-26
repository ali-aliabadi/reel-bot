"""Publish stage, pure core: who may post, what a post says, and reading the
platforms' replies.

A video reaches a platform only with ali's recorded story pick and a finished
render (the human pick rule; the `posts` table enforces the pick too). The
upload calls live in `youtube`, `instagram` and `tiktok`; `publish_write` runs
them and records each post.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

from reel_bot.script import Script
from reel_bot.videos import StageRun, Video

PLATFORMS = ("youtube", "instagram", "tiktok")
TITLE_MAX = 100  # YouTube's limit
CAPTION_MAX = 2200  # Instagram's and TikTok's limit
TAGS = ("fables", "shorts", "storytime")
COLLECTION_TAGS = {
    "Aesop": ("aesop", "aesopsfables"),
    "Panchatantra": ("panchatantra", "indianfolktales"),
    "Kalilah and Dimnah": ("kalilahanddimnah", "bidpai"),
    "Masnavi": ("rumi", "masnavi"),
}
_ID = re.compile(r"^[A-Za-z0-9_.~-]{1,200}$")
CODE_MAX = 2000


class PublishError(Exception):
    pass


@dataclass(frozen=True)
class Post:
    title: str
    description: str  # the hook, the source, the AI disclosure and tags
    tags: tuple[str, ...] = ()  # YouTube's search tags (not shown to viewers)

    def caption(self) -> str:
        """The title and description as one caption (Instagram, TikTok)."""
        return f"{self.title}\n\n{self.description}"[:CAPTION_MAX]


@dataclass(frozen=True)
class Uploader:
    """One platform: `access(saved sign-in)` returns an access token and the sign-in
    to save (some platforms rotate it); `upload(token, sign-in, mp4, post)` posts
    and returns the platform's ID for the post."""

    access: Callable[[dict[str, str]], tuple[str, dict[str, str]]]
    upload: Callable[[str, dict[str, str], Path, Post], str]


def check_ready(video: Video, runs: Mapping[str, StageRun]) -> None:
    if not (video.story_id and video.chosen_by and video.chosen_at):
        raise PublishError(f"video {video.id} has no recorded story pick, so it can't be posted")
    if "render" not in runs:
        raise PublishError(f"video {video.id} has no render yet")


def post_for(script: Script, collection: str = "") -> Post:
    """The model's title and caption when it wrote them, else the story's title and
    hook; the source, the AI disclosure and the tags are always ours."""
    title = re.sub(r"[<>]", "", script.post.title or script.title).strip()
    suffix = " #shorts"
    hashtags = dict.fromkeys([*script.post.hashtags, *COLLECTION_TAGS.get(collection, ()), *TAGS])
    if len(title) + len(suffix) > TITLE_MAX:
        title = title[: TITLE_MAX - len(suffix) - 1].rstrip() + "…"
    description = "\n\n".join(
        [
            script.post.caption or script.hook,
            f"Retold from {script.source}, {script.source_ref}. "
            "Narrated by Tara; the pictures are made with AI.",
            " ".join("#" + t for t in hashtags),
        ]
    )
    tags = ("fable", "moral story", script.title, collection, *script.post.hashtags)
    return Post(title + suffix, description, tuple(t for t in tags if t))


def code_from(pasted: str, state: str) -> str:
    """The sign-in code from the address the browser landed on after approval."""
    try:
        query = parse_qs(urlparse(pasted.strip()).query)
    except ValueError as err:
        raise PublishError("that is not a web address") from err
    if query.get("state") != [state]:
        raise PublishError("that address is not from this sign-in (the state does not match)")
    if "error" in query:
        raise PublishError(f"the sign-in was refused: {query['error'][0][:80]}")
    codes = query.get("code", [])
    if len(codes) != 1 or not 0 < len(codes[0]) <= CODE_MAX:
        raise PublishError("that address has no sign-in code")
    return codes[0]


def obj(raw: bytes, what: str) -> dict[str, object]:
    """A reply's JSON object; anything else raises PublishError."""
    try:
        value: object = json.loads(raw)
    except (ValueError, RecursionError) as err:
        raise PublishError(f"{what}: the reply is not JSON") from err
    if not isinstance(value, dict):
        raise PublishError(f"{what}: the reply is not a JSON object")
    return cast(dict[str, object], value)


def text(value: object, *path: str, what: str) -> str:
    """The non-empty string at path inside a reply."""
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise PublishError(f"{what}: the reply has no {'.'.join(path)}")
        value = cast(dict[str, object], value)[key]
    if not isinstance(value, str) or not value:
        raise PublishError(f"{what}: the reply's {'.'.join(path)} is not text")
    return value


def ident(value: str, what: str) -> str:
    """A platform ID that is safe to put in a URL path."""
    if not _ID.match(value):
        raise PublishError(f"{what}: odd ID in the reply")
    return value


def https_on(url: str, domain: str, what: str) -> str:
    """An upload URL from a reply, accepted only on the platform's own domain."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except ValueError as err:
        raise PublishError(f"{what}: the upload URL is not a URL") from err
    if parsed.scheme != "https" or not (host == domain or host.endswith("." + domain)):
        raise PublishError(f"{what}: the upload URL is not on {domain}")
    return url
