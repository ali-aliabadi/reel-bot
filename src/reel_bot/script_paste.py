"""The script written by hand in a chat app (ChatGPT), pure core: the brief Tara
pastes into the chat, what to tell the chat when its script has problems, and
reading back the answer she pastes into Telegram.

The chat answers in labelled plain text (HOOK:, SCENE:, PICTURE: ...), not JSON:
a person can read and fix it, and it is shorter, so it fits in one Telegram
message (Telegram splits a longer one and only the first part reaches the bot).
An answer in the older JSON format is still read.
"""

import re

from reel_bot import script
from reel_bot.stories import Story
from reel_bot.videos import Video

ANSWER_MAX = 3500  # characters; Telegram splits a message over 4096
CHAT_FORMAT = """HOOK: the hook
SCENE: what is read aloud in scene 1
PICTURE: one sentence on what scene 1's picture shows
SCENE: what is read aloud in scene 2
PICTURE: one sentence on what scene 2's picture shows
(one SCENE and PICTURE pair for every scene)
LESSON: the lesson, or leave this line out when there is none
TITLE: the post's title
CAPTION: the post's caption
HASHTAGS: word, word, word"""
_STRING_END = re.compile(r'\s*(?:[:}\]]|,\s*["{\[]|\Z)')
_NONE = {"", "none", "n/a", "-", "no lesson"}
# A label at the start of a line, allowing markdown around it (**HOOK:**, ### Scene 2:).
_LABEL = re.compile(
    r"^[\s>*#_`-]*(hook|scene|picture|lesson|title|caption|hashtags)\b[^:\n]{0,4}:[\s*_]*(.*)$",
    re.IGNORECASE,
)


def chat_prompt(story: Story, video: Video) -> str:
    """The whole brief as one message for a chat app, ending with the reply format."""
    return (
        f"{script.SYSTEM}\n\n{script.prompt(story, video)}\n\n"
        f"Keep your whole answer under {ANSWER_MAX} characters. Answer in plain text "
        f"(no JSON, no code block), in exactly this format, nothing before or after it:\n"
        f"{CHAT_FORMAT}"
    )


def chat_fix(found: list[str]) -> str:
    """What to tell the chat app when its script has problems."""
    return (
        "That script has these problems:\n- "
        + "\n- ".join(found)
        + "\nWrite it again without them, in the same plain-text format "
        f"(HOOK, SCENE, PICTURE ... HASHTAGS), under {ANSWER_MAX} characters."
    )


def _fields(raw: str) -> list[tuple[str, str]]:
    """(label, text) for each labelled line; an unlabelled line continues the last one."""
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        found = _LABEL.match(line)
        if found:
            out.append((found[1].lower(), found[2].strip(" *_")))
        elif out and line.strip():
            label, text = out[-1]
            out[-1] = (label, f"{text} {line.strip()}".strip())
    return out


def _escape_quotes(raw: str) -> str:
    """JSON with dialogue quotes inside its strings escaped: a chat writes
    "text": "He said, "Run!" and ran." A quote ends a string only when JSON can go
    on after it (a ':', '}' or ']', or a ',' and then a new value)."""
    text, out, inside = raw.replace("“", '"').replace("”", '"'), list[str](), False
    escaped = False  # the last character was a backslash that escapes this one
    for i, char in enumerate(text):
        if char == '"' and not escaped:
            if not inside or _ends_string(text, i + 1):
                inside = not inside
            else:
                out.append("\\")
        escaped = inside and char == "\\" and not escaped
        out.append(char)
    return "".join(out)


def _ends_string(text: str, at: int) -> bool:
    return bool(_STRING_END.match(text, at))


def parse(raw: str, story: Story, video: Video) -> script.Script:
    """The pasted answer as a Script, from labelled plain text or else JSON.
    Raises ScriptError with a reason a person (and the chat) can act on."""
    fields = _fields(raw)
    if not fields:
        try:
            return script.parse(raw, story, video)
        except script.ScriptError:
            pass
        try:
            return script.parse(_escape_quotes(raw), story, video)
        except script.ScriptError as err:
            raise script.ScriptError(
                "it is not in the format asked for: it has no HOOK, SCENE and PICTURE lines"
            ) from err
    paragraphs: list[dict[str, str]] = []
    first: dict[str, str] = {}
    for label, text in fields:
        if label == "scene":
            paragraphs.append({"text": text, "picture": ""})
        elif label == "picture" and paragraphs:
            paragraphs[-1]["picture"] = text
        else:
            first.setdefault(label, text)
    if "hook" not in first or not paragraphs:
        raise script.ScriptError("it needs a HOOK line and SCENE lines, each with a PICTURE")
    if "title" not in first and "caption" not in first:
        raise script.ScriptError(
            "it stops before the TITLE and CAPTION lines, so it was probably cut off; "
            f"write it shorter, under {ANSWER_MAX} characters"
        )
    data = {
        "hook": first["hook"],
        "paragraphs": paragraphs,
        "lesson": "" if first.get("lesson", "").lower().strip(" .") in _NONE else first["lesson"],
        "post": {
            "title": first.get("title", ""),
            "caption": first.get("caption", ""),
            "hashtags": re.split(r"[\s,]+", first.get("hashtags", "")),
        },
    }
    return script.build(data, story, video)
