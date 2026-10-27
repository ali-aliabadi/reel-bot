"""Script stage, pure core: the prompt, the reply's shape, and the checks.

A script is the retelling Tara reads aloud, split into paragraphs, each with one
picture prompt for the visuals stage. The video's length and framing settings
decide the word budget and whether a closing work lesson is written; this
module reads those settings from tables and never branches on their names
elsewhere. The model call itself lives in `script_write`; `chat_prompt` is the
same brief as one message to paste into a chat app (ChatGPT) by hand.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from reel_bot.stories import Story
from reel_bot.videos import Video

# Tara reads at about 2.3 words a second: short is about 45 s, long 61 to 90 s.
WORDS = {"short": (95, 120), "long": (150, 200)}
# What the check accepts: the brief asks for WORDS, but a script a little long is
# still fine to read (150 words is about 65 s), so no fix is asked for it.
WORDS_ACCEPTED = {"short": (95, 150), "long": (150, 200)}
_CURLY = str.maketrans("“”", '""')
LESSON = {"fable": False, "lesson": True}
PARAGRAPHS = (3, 6)
HOOK_MAX_WORDS = 14
POST_TITLE_MAX, POST_CAPTION_MAX, HASHTAGS_MAX = 60, 300, 3
_HASHTAG = re.compile(r"^[a-z][a-z0-9]{1,29}$")
PICTURE_STYLE = (
    "Persian miniature painting, flat perspective, rich lapis blue and gold, "
    "fine ornamental border, soft daylight, no text or letters"
)


class ScriptError(Exception):
    pass


@dataclass(frozen=True)
class Paragraph:
    text: str  # what Tara reads
    picture: str  # what the picture for this paragraph shows (style is added later)


@dataclass(frozen=True)
class PostText:
    """The model's words for the post; empty fields fall back to the defaults."""

    title: str = ""
    caption: str = ""
    hashtags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Script:
    video_id: str
    story_id: str
    title: str
    hook: str  # the first line, read before paragraph one
    paragraphs: tuple[Paragraph, ...]
    lesson: str  # "" when the framing has no lesson
    source: str
    source_ref: str
    post: PostText = PostText()

    def spoken(self) -> str:
        """Everything Tara reads, in order."""
        parts = [self.hook, *(p.text for p in self.paragraphs)]
        if self.lesson:
            parts.append(self.lesson)
        return "\n\n".join(parts)

    def words(self) -> int:
        return len(self.spoken().split())


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "picture": {"type": "string"}},
                "required": ["text", "picture"],
                "additionalProperties": False,
            },
        },
        "lesson": {"type": "string"},
        "post": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "caption": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "caption", "hashtags"],
            "additionalProperties": False,
        },
    },
    "required": ["hook", "paragraphs", "lesson"],
    "additionalProperties": False,
}

SYSTEM = """You retell old fables for a YouTube Shorts channel for adults. A woman
narrates each script in her own voice over still pictures, so write for the ear:
short sentences, plain modern English, concrete images, no stage directions,
no emoji, no hashtags.

The hook is one spoken line that makes a stranger keep listening; it must not
give away the ending. Then tell the tale in order in a few paragraphs, each one
a single scene that one picture can show. Stay true to the tale as the named
source tells it: keep its characters, events and ending. Write your own words;
never quote a modern translation.

For each paragraph, describe its picture in one sentence: who is in it, where,
doing what. No words, letters or signs in pictures. Never describe real people.

Last, write the post that goes with the video. "title": at most 60
characters, makes a scroller curious without giving away the ending. "caption":
one or two sentences, at most 300 characters. Neither has hashtags, links, @
names or emoji. "hashtags": up to 3 lowercase words without "#" about this
tale's name or theme, such as "sourgrapes"."""


def prompt(story: Story, video: Video) -> str:
    """The user message for this story and video's settings."""
    low, high = WORDS[video.length]
    lesson = (
        "After the tale, write a lesson of one or two sentences that carries the "
        "fable's point into working life today (a team, a boss, a deal), without "
        "preaching."
        if LESSON[video.framing]
        else 'Write no lesson after the tale: set "lesson" to an empty string.'
    )
    return (
        f"Tale: {story.title} ({story.collection}).\n"
        f"Retell it from: {story.source}, {story.source_ref}.\n"
        f"Length: {low} to {high} spoken words in total, hook and lesson included.\n"
        f"Paragraphs: {PARAGRAPHS[0]} to {PARAGRAPHS[1]}. "
        f"Hook: at most {HOOK_MAX_WORDS} words.\n"
        f"{lesson}"
    )


CHAT_FORMAT = (
    '{"hook": "...", "paragraphs": [{"text": "...", "picture": "..."}], "lesson": "", '
    '"post": {"title": "...", "caption": "...", "hashtags": ["..."]}}'
)


def chat_prompt(story: Story, video: Video) -> str:
    """The whole brief as one message for a chat app, ending with the reply format."""
    return (
        f"{SYSTEM}\n\n{prompt(story, video)}\n\n"
        f"Answer with only this JSON, nothing before or after it:\n{CHAT_FORMAT}"
    )


def chat_fix(found: list[str]) -> str:
    """What to tell the chat app when its script has problems."""
    return (
        "That script has these problems:\n- "
        + "\n- ".join(found)
        + "\nWrite it again without them. Answer with only the JSON."
    )


def _loads(text: str) -> object:
    """JSON, or the same with curly double quotes made straight: a chat answer
    copied as plain text instead of from its code block comes with them."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(text.translate(_CURLY))


def parse(raw: str, story: Story, video: Video) -> Script:
    """The model's JSON reply as a Script, ignoring any text or code fence around
    the object (a pasted chat answer). Raises ScriptError on a bad shape."""
    try:
        data = cast(dict[str, Any], _loads(raw[raw.find("{") : raw.rfind("}") + 1]))
        paragraphs = tuple(
            Paragraph(str(p["text"]).strip(), str(p["picture"]).strip())
            for p in cast(list[dict[str, Any]], data["paragraphs"])
        )
        hook, lesson = str(data["hook"]).strip(), str(data["lesson"]).strip()
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError) as err:
        raise ScriptError("the model's reply is not a script") from err
    return Script(
        video_id=video.id,
        story_id=story.id,
        title=story.title,
        hook=hook,
        paragraphs=paragraphs,
        lesson=lesson,
        source=story.source,
        source_ref=story.source_ref,
        post=post_text(data.get("post")),
    )


def _plain(value: object, limit: int) -> str:
    """One line of model text with no tags, links or names, or "" when it has them."""
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return "" if len(text) > limit or re.search(r"[#@<>]|https?:|www\.", text) else text


def post_text(raw: object) -> PostText:
    """The reply's post part, keeping only what is safe to publish; never raises."""
    if not isinstance(raw, dict):
        return PostText()
    data = cast(dict[str, object], raw)
    tags = data.get("hashtags")
    items = cast(list[object], tags) if isinstance(tags, list) else []
    words = [t.strip().lstrip("#").lower() for t in items if isinstance(t, str)]
    kept = tuple(dict.fromkeys(w for w in words if _HASHTAG.match(w)))[:HASHTAGS_MAX]
    return PostText(
        _plain(data.get("title"), POST_TITLE_MAX),
        _plain(data.get("caption"), POST_CAPTION_MAX),
        kept,
    )


def problems(script: Script, video: Video) -> list[str]:
    """What is wrong with a script for this video; empty when it's usable."""
    out: list[str] = []
    low, high = WORDS[video.length]
    least, most = WORDS_ACCEPTED[video.length]
    if not least <= script.words() <= most:
        out.append(f"it has {script.words()} words; it needs {low} to {high}")
    if not PARAGRAPHS[0] <= len(script.paragraphs) <= PARAGRAPHS[1]:
        out.append(
            f"it has {len(script.paragraphs)} paragraphs; it needs {PARAGRAPHS[0]}-{PARAGRAPHS[1]}"
        )
    if not script.hook or len(script.hook.split()) > HOOK_MAX_WORDS:
        out.append(f"the hook must be 1 to {HOOK_MAX_WORDS} words")
    if any(not p.text or not p.picture for p in script.paragraphs):
        out.append("every paragraph needs text and a picture")
    if LESSON[video.framing] != bool(script.lesson):
        out.append(
            "the lesson must be there" if LESSON[video.framing] else "there must be no lesson"
        )
    return out


def to_json(script: Script) -> str:
    return json.dumps(
        {
            "video_id": script.video_id,
            "story_id": script.story_id,
            "title": script.title,
            "hook": script.hook,
            "paragraphs": [{"text": p.text, "picture": p.picture} for p in script.paragraphs],
            "lesson": script.lesson,
            "source": script.source,
            "source_ref": script.source_ref,
            "post": {
                "title": script.post.title,
                "caption": script.post.caption,
                "hashtags": list(script.post.hashtags),
            },
            "picture_style": PICTURE_STYLE,
        },
        ensure_ascii=False,
        indent=2,
    )


def from_json(raw: str) -> Script:
    data = cast(dict[str, Any], json.loads(raw))
    return Script(
        video_id=data["video_id"],
        story_id=data["story_id"],
        title=data["title"],
        hook=data["hook"],
        paragraphs=tuple(
            Paragraph(p["text"], p["picture"])
            for p in cast(list[dict[str, str]], data["paragraphs"])
        ),
        lesson=data["lesson"],
        source=data["source"],
        source_ref=data["source_ref"],
        post=post_text(data.get("post")),  # scripts saved before posts had text have none
    )
