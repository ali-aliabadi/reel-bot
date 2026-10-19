import dataclasses

import pytest

from reel_bot import captions
from reel_bot.captions import Cue, Section, Word
from reel_bot.script import Paragraph, Script


def heard(*items: tuple[str, float, float]) -> list[Word]:
    return [Word(t, s, e) for t, s, e in items]


def test_align_keeps_the_script_spelling_and_takes_heard_times() -> None:
    words, matched = captions.align(
        ["Dimnah,", "the", "jackal."],
        heard(("dimnah", 0.5, 0.9), ("the", 1.0, 1.1), ("Jackal", 1.2, 1.6)),
    )
    assert words == (Word("Dimnah,", 0.5, 0.9), Word("the", 1.0, 1.1), Word("jackal.", 1.2, 1.6))
    assert matched == 1.0


def test_align_spreads_unmatched_words_across_the_gap() -> None:
    words, matched = captions.align(
        ["a", "Kalilah", "Dimnah", "b"],
        heard(("a", 0.0, 1.0), ("colleague", 1.0, 1.5), ("b", 3.0, 3.5)),
    )
    assert [(w.start, w.end) for w in words] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 3.5)]
    assert matched == 0.5


def test_align_guesses_at_both_ends() -> None:
    words, _ = captions.align(
        ["x", "y", "mid", "z"],
        heard(("mid", 1.0, 1.5), ("tail", 1.5, 1.6)),
    )
    assert words[0].start == pytest.approx(0.4)
    assert words[1].end == 1.0
    assert words[3].start == 1.5
    assert words[3].end == pytest.approx(1.8)


def test_align_with_nothing_matched_spreads_over_the_take() -> None:
    words, matched = captions.align(["one", "two"], heard(("uh", 1.0, 2.0), ("um", 2.0, 3.0)))
    assert matched == 0.0
    assert (words[0].start, words[-1].end) == (1.0, 3.0)


@pytest.mark.parametrize(("said", "h"), [(["a"], []), ([], heard(("a", 0.0, 1.0)))])
def test_align_refuses_empty_input(said: list[str], h: list[Word]) -> None:
    with pytest.raises(captions.CaptionsError):
        captions.align(said, h)


def test_cues_split_at_punctuation_pauses_and_size() -> None:
    words = [
        Word("Once", 0.0, 0.2),
        Word("a", 0.2, 0.3),
        Word("monkey,", 0.3, 0.7),
        Word("wise", 0.8, 1.0),
        Word("and", 1.0, 1.1),
        Word("tremendously", 1.1, 1.6),
        Word("kind.", 2.5, 2.9),
        Word("“Why?”", 3.0, 3.4),
    ]
    assert captions.cues(words) == (
        Cue("Once a monkey", 0.0, 0.8),
        Cue("wise and", 0.8, 1.1),
        Cue("tremendously", 1.1, 1.6),
        Cue("kind", 2.5, 3.0),
        Cue("Why?", 3.0, 3.4),
    )


def test_sections_follow_the_parts() -> None:
    words = [Word(str(i), float(i), i + 0.5) for i in range(6)]
    named = [("hook", "a b"), ("p1", "c d e"), ("lesson", "f")]
    assert captions.sections(named, words) == (
        Section("hook", 0.0, 1.5),
        Section("p1", 2.0, 4.5),
        Section("lesson", 5.0, 5.5),
    )
    with pytest.raises(captions.CaptionsError, match="lesson"):
        captions.sections(named, words[:5])


SCRIPT = Script(
    video_id="vid_X",
    story_id="aesop-fox",
    title="The Fox and the Grapes",
    hook="A fox wanted grapes.",
    paragraphs=(
        Paragraph("He jumped and missed.", "A fox leaping."),
        Paragraph("He walked away, proud.", "A fox walking off."),
    ),
    lesson="",
    source="Townsend",
    source_ref="p. 1",
)


def test_build_and_json_round_trip(snapshot: object) -> None:
    said = SCRIPT.spoken().split()
    h = [Word(w.strip(".,").lower(), i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(said)]
    c = captions.build("vid_X", SCRIPT, h)
    assert [s.name for s in c.sections] == ["hook", "p1", "p2"]
    assert c.matched == 1.0
    raw = captions.to_json(c)
    assert captions.from_json(raw) == captions.from_json(captions.to_json(captions.from_json(raw)))
    assert callable(snapshot)
    snapshot("captions.json", raw + "\n")


def test_parts_include_the_lesson_only_when_written() -> None:
    assert [n for n, _ in captions.parts(SCRIPT)] == ["hook", "p1", "p2"]
    with_lesson = dataclasses.replace(SCRIPT, lesson="Own your misses.")
    assert captions.parts(with_lesson)[-1] == ("lesson", "Own your misses.")
