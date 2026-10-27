import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from reel_bot import script
from reel_bot.script import Paragraph, Script
from reel_bot.stories import Story
from reel_bot.videos import Video

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
STORY = Story(
    "panchatantra-monkey-and-crocodile",
    "monkey-and-crocodile",
    "The Monkey and the Crocodile",
    "Panchatantra",
    "Arthur W. Ryder, The Panchatantra (1925)",
    "Book IV",
)
SHORT_FABLE = Video("vid_01JAAAAAAAAAAAAAAAAAAAAAAA", NOW, "fable", "short")
LONG_LESSON = Video("vid_01JAAAAAAAAAAAAAAAAAAAAAAA", NOW, "lesson", "long")


def reply(words_per_paragraph: int = 25, paragraphs: int = 4, lesson: str = "") -> str:
    para = " ".join(["word"] * words_per_paragraph)
    return json.dumps(
        {
            "hook": "A monkey trusted a friend with teeth.",
            "paragraphs": [{"text": para, "picture": "A monkey in a fig tree by a river."}]
            * paragraphs,
            "lesson": lesson,
            "post": {
                "title": "He trusted the wrong friend",
                "caption": "A monkey, a crocodile and one very bad river crossing.",
                "hashtags": ["#MonkeyAndCrocodile", "trust", "trust"],
            },
        }
    )


def test_prompt_reads_the_settings(snapshot: Callable[[str, str], None]) -> None:
    snapshot("script_prompt_short_fable.txt", script.prompt(STORY, SHORT_FABLE))
    snapshot("script_prompt_long_lesson.txt", script.prompt(STORY, LONG_LESSON))
    assert "95 to 120" in script.prompt(STORY, SHORT_FABLE)
    assert "working life" in script.prompt(STORY, LONG_LESSON)


def test_chat_prompt_is_the_whole_brief_ending_with_the_format() -> None:
    chat = script.chat_prompt(STORY, SHORT_FABLE)
    assert chat.startswith(script.SYSTEM)
    assert script.prompt(STORY, SHORT_FABLE) in chat
    assert chat.endswith(script.CHAT_FORMAT)
    assert len(chat) < 4000  # one Relay code block
    fix = script.chat_fix(["it has 130 words; it needs 95 to 120"])
    assert "- it has 130 words; it needs 95 to 120\n" in fix


def test_parse_takes_a_pasted_chat_answer() -> None:
    pasted = f"Sure! Here it is:\n```json\n{reply()}\n```\nWant changes?"
    assert script.parse(pasted, STORY, SHORT_FABLE) == script.parse(reply(), STORY, SHORT_FABLE)


def test_parse_and_check_a_good_script(snapshot: Callable[[str, str], None]) -> None:
    s = script.parse(reply(), STORY, SHORT_FABLE)
    assert s.words() == 7 + 100
    assert script.problems(s, SHORT_FABLE) == []
    assert s.source == STORY.source
    snapshot("script_short_fable.json", script.to_json(s))
    assert script.from_json(script.to_json(s)) == s


@pytest.mark.parametrize(
    ("raw", "video", "expect"),
    [
        (reply(words_per_paragraph=40), SHORT_FABLE, "words"),
        (reply(paragraphs=2, words_per_paragraph=50), SHORT_FABLE, "paragraphs"),
        (reply(lesson="Trust slowly."), SHORT_FABLE, "no lesson"),
        (reply(words_per_paragraph=40), LONG_LESSON, "lesson must be there"),
    ],
)
def test_problems_are_named(raw: str, video: Video, expect: str) -> None:
    found = script.problems(script.parse(raw, STORY, video), video)
    assert any(expect in p for p in found), found


def test_a_little_long_is_accepted_but_too_long_asks_for_the_brief() -> None:
    ok = script.parse(reply(words_per_paragraph=35), STORY, SHORT_FABLE)  # 147 words
    assert script.problems(ok, SHORT_FABLE) == []
    long = script.parse(reply(words_per_paragraph=37), STORY, SHORT_FABLE)  # 155 words
    assert script.problems(long, SHORT_FABLE) == ["it has 155 words; it needs 95 to 120"]


def test_parse_takes_curly_quotes() -> None:
    curly = re.sub(r'"([^"]*)"', "“\\1”", reply())
    assert script.parse(curly, STORY, SHORT_FABLE) == script.parse(reply(), STORY, SHORT_FABLE)


def test_long_hook_and_missing_picture() -> None:
    s = script.parse(reply(), STORY, SHORT_FABLE)
    bad = replace(
        s,
        hook=" ".join(["very"] * 20),
        paragraphs=(*s.paragraphs[:3], Paragraph("text " * 25, "")),
    )
    found = script.problems(bad, SHORT_FABLE)
    assert any("hook" in p for p in found)
    assert any("picture" in p for p in found)


@pytest.mark.parametrize("raw", ["not json", "{}", '{"hook": "x", "paragraphs": 3, "lesson": ""}'])
def test_parse_rejects_bad_replies(raw: str) -> None:
    with pytest.raises(script.ScriptError):
        script.parse(raw, STORY, SHORT_FABLE)


def test_spoken_includes_the_lesson() -> None:
    s = Script("v", "s", "t", "Hook.", (Paragraph("One.", "p"),), "Lesson.", "src", "ref")
    assert s.spoken() == "Hook.\n\nOne.\n\nLesson."


def test_parse_keeps_the_post_text() -> None:
    post = script.parse(reply(), STORY, SHORT_FABLE).post
    assert post.title == "He trusted the wrong friend"
    assert post.hashtags == ("monkeyandcrocodile", "trust")


ODD_POSTS: list[object] = [
    None,
    "post",
    [],
    {"title": 7, "caption": None, "hashtags": "trust"},
    {"title": "x" * 61, "caption": "y" * 301, "hashtags": [1, None, {}]},
    {"title": "Go to https://spam.example", "caption": "Follow @someone #ad"},
    {"title": "www.spam.example", "caption": "<script>", "hashtags": ["two words", "#", "ok!"]},
    {"hashtags": ["a" * 31, "1abc", ""]},
]


@pytest.mark.parametrize("raw", ODD_POSTS)
def test_odd_post_text_is_dropped_not_fatal(raw: object) -> None:
    assert script.post_text(raw) == script.PostText()


def test_post_text_keeps_at_most_three_hashtags() -> None:
    got = script.post_text({"title": " Two\nlines ", "hashtags": ["a1", "b2", "c3", "d4"]})
    assert got == script.PostText("Two lines", "", ("a1", "b2", "c3"))


def test_scripts_saved_before_post_text_still_load() -> None:
    data = json.loads(script.to_json(script.parse(reply(), STORY, SHORT_FABLE)))
    del data["post"]
    assert script.from_json(json.dumps(data)).post == script.PostText()
