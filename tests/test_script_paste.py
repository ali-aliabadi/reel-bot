import contextlib
import random
import string
from datetime import UTC, datetime

import pytest

from reel_bot import script, script_paste
from reel_bot.stories import Story
from reel_bot.videos import Video

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
STORY = Story(
    "panchatantra-mouse-and-bull",
    "mouse-and-bull",
    "The Mouse and the Bull",
    "Aesop",
    "Townsend",
    "p. 1",
)
SHORT_FABLE = Video("vid_01JAAAAAAAAAAAAAAAAAAAAAAA", NOW, "fable", "short")
TEXT = " ".join(["word"] * 25)


def plain(scenes: int = 4, extra: str = "") -> str:
    body = "".join(
        f"SCENE: {TEXT}\nPICTURE: A mouse by a bull, scene {i}.\n" for i in range(scenes)
    )
    return (
        "HOOK: A mouse bit a bull and lived.\n"
        f"{body}{extra}"
        "TITLE: The smallest one wins\n"
        "CAPTION: A bull, a mouse and one hole in the wall.\n"
        "HASHTAGS: #mouseandbull, aesop, pride"
    )


def test_chat_prompt_is_the_whole_brief_ending_with_the_plain_format() -> None:
    chat = script_paste.chat_prompt(STORY, SHORT_FABLE)
    assert chat.startswith(script.SYSTEM)
    assert script.prompt(STORY, SHORT_FABLE) in chat
    assert chat.endswith(script_paste.CHAT_FORMAT)
    assert "no JSON" in chat
    assert len(chat) < 4000  # one Relay code block
    fix = script_paste.chat_fix(["it has 130 words; it needs 95 to 120"])
    assert "- it has 130 words; it needs 95 to 120\n" in fix
    assert "plain-text format" in fix


def test_parse_reads_plain_text() -> None:
    s = script_paste.parse(plain(), STORY, SHORT_FABLE)
    assert s.hook == "A mouse bit a bull and lived."
    assert len(s.paragraphs) == 4
    assert s.paragraphs[3].picture == "A mouse by a bull, scene 3."
    assert s.lesson == ""
    assert s.post == script.PostText(
        "The smallest one wins",
        "A bull, a mouse and one hole in the wall.",
        ("mouseandbull", "aesop", "pride"),
    )
    assert script.problems(s, SHORT_FABLE) == []


def test_parse_takes_chat_markdown_and_wrapped_lines() -> None:
    messy = (
        "Here is your script!\n\n"
        + plain()
        .replace("HOOK:", "**Hook:**")
        .replace("SCENE:", "### Scene 1:", 1)
        .replace(TEXT, f"{TEXT[:19]}\n{TEXT[20:]}", 1)
        + "\n\nWant another version?"
    )
    assert script_paste.parse(messy, STORY, SHORT_FABLE) == script_paste.parse(
        plain(), STORY, SHORT_FABLE
    )


@pytest.mark.parametrize("lesson", ["LESSON: none\n", "LESSON:\n", "LESSON: N/A.\n"])
def test_a_lesson_of_none_is_no_lesson(lesson: str) -> None:
    assert script_paste.parse(plain(extra=lesson), STORY, SHORT_FABLE).lesson == ""


def test_parse_still_reads_json_in_a_code_fence() -> None:
    fenced = (
        '```json\n{"hook": "h", "paragraphs": [{"text": "t", "picture": "p"}], "lesson": ""}\n```'
    )
    assert script_paste.parse(fenced, STORY, SHORT_FABLE).paragraphs == (
        script.Paragraph("t", "p"),
    )


def test_parse_repairs_dialogue_quotes_in_json() -> None:
    # Tara's real ChatGPT answers (Nov 8): dialogue in plain double quotes, unescaped.
    said = 'The mouse squeaked, "The great do not always prevail," and ran.'
    raw = (
        '{\n"hook": "Power means nothing.",\n"paragraphs": [\n{\n'
        f'"text": "{said}",\n"picture": "A mouse in a wall."\n}}\n],\n"lesson": ""\n}}'
    )
    s = script_paste.parse(raw, STORY, SHORT_FABLE)
    assert s.paragraphs == (script.Paragraph(said, "A mouse in a wall."),)
    half = raw.replace('"The great', '\\"The great')  # one quote escaped, one not
    assert script_paste.parse(half, STORY, SHORT_FABLE) == s
    curly = raw.replace('"', "”")  # pasted as text, with smart quotes everywhere
    assert script_paste.parse(curly, STORY, SHORT_FABLE) == s


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("Sorry, I can't help with that.", "no HOOK, SCENE and PICTURE lines"),
        ('{"hook": "cut off', "no HOOK, SCENE and PICTURE lines"),
        ("SCENE: text\nPICTURE: p\nTITLE: t", "needs a HOOK line"),
        ("HOOK: h\nTITLE: t", "needs a HOOK line"),
        (plain().split("TITLE:")[0], "probably cut off"),
    ],
)
def test_parse_says_what_is_missing(raw: str, reason: str) -> None:
    with pytest.raises(script.ScriptError, match=reason):
        script_paste.parse(raw, STORY, SHORT_FABLE)


def test_a_scene_without_picture_is_a_problem_not_a_crash() -> None:
    raw = plain().replace("PICTURE: A mouse by a bull, scene 0.\n", "")
    found = script.problems(script_paste.parse(raw, STORY, SHORT_FABLE), SHORT_FABLE)
    assert "every paragraph needs text and a picture" in found


def test_odd_plain_text_raises_only_script_error() -> None:
    rng = random.Random(3)
    labels = ["HOOK:", "SCENE:", "PICTURE:", "LESSON:", "TITLE:", "CAPTION:", "HASHTAGS:", "**", ""]
    for _ in range(300):
        lines = [
            rng.choice(labels)
            + "".join(rng.choices(string.printable + "é🦊“”", k=rng.randint(0, 30)))
            for _ in range(rng.randint(0, 20))
        ]
        with contextlib.suppress(script.ScriptError):
            script_paste.parse("\n".join(lines), STORY, SHORT_FABLE)
