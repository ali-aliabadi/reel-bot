"""Script stage, pure core: the prompt, the reply's shape, and the checks.

A script is the retelling Tara reads aloud, split into paragraphs, each with one
picture prompt for the visuals stage. The video's length and framing settings
decide the word budget and whether a closing work lesson is written; this
module reads those settings from tables and never branches on their names
elsewhere. The model call itself lives in `script_write`.
"""

import json
from dataclasses import dataclass
from typing import Any, cast

from reel_bot.stories import Story
from reel_bot.videos import Video

# Tara reads at about 2.3 words a second: short is about 45 s, long 61 to 90 s.
WORDS = {"short": (95, 120), "long": (150, 200)}
LESSON = {"fable": False, "lesson": True}
PARAGRAPHS = (3, 6)
HOOK_MAX_WORDS = 14
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
class Script:
    video_id: str
    story_id: str
    title: str
    hook: str  # the first line, read before paragraph one
    paragraphs: tuple[Paragraph, ...]
    lesson: str  # "" when the framing has no lesson
    source: str
    source_ref: str

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
doing what. No words, letters or signs in pictures. Never describe real people."""


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


def parse(raw: str, story: Story, video: Video) -> Script:
    """The model's JSON reply as a Script. Raises ScriptError on a bad shape."""
    try:
        data = cast(dict[str, Any], json.loads(raw))
        paragraphs = tuple(
            Paragraph(str(p["text"]).strip(), str(p["picture"]).strip())
            for p in cast(list[dict[str, Any]], data["paragraphs"])
        )
        hook, lesson = str(data["hook"]).strip(), str(data["lesson"]).strip()
    except (ValueError, KeyError, TypeError) as err:
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
    )


def problems(script: Script, video: Video) -> list[str]:
    """What is wrong with a script for this video; empty when it's usable."""
    out: list[str] = []
    low, high = WORDS[video.length]
    if not low <= script.words() <= high:
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
    )
