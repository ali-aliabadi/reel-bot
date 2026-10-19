"""Captions stage, pure core: put the script's words on Tara's timeline.

The transcription gives times for what was heard; the script gives what was
meant. `align` keeps the script's words (spelling, names, punctuation) and takes
their times from the matching heard words, spreading unmatched words evenly
across the gap. `cues` groups the timed words into the short captions burned in
by the render stage, and `sections` gives when each part (hook, paragraphs,
lesson) is spoken, so pictures change with the story. The API call lives in
`captions_write`.
"""

import difflib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from reel_bot.script import Script

MAX_WORDS = 3  # words on screen at once
MAX_CHARS = 16  # one line at the caption size on a 1080 px wide frame
PAUSE_S = 0.35  # a silence this long starts a new caption
GUESS_WORD_S = 0.3  # length given to an unmatched word with no neighbour on that side
_HOLD = ",.;:"  # dropped from the end of a caption; ? and ! stay
_QUOTES = "\"'\u201c\u201d\u2018\u2019"


class CaptionsError(Exception):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Cue:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Section:
    name: str  # "hook", "p1".."pN", "lesson"
    start: float
    end: float


@dataclass(frozen=True)
class Captions:
    video_id: str
    words: tuple[Word, ...]
    cues: tuple[Cue, ...]
    sections: tuple[Section, ...]
    matched: float  # share of script words found in the take, 0 to 1


def parts(script: Script) -> list[tuple[str, str]]:
    """The spoken parts in order, named as in `Section.name`."""
    out = [("hook", script.hook)]
    out += [(f"p{i}", p.text) for i, p in enumerate(script.paragraphs, 1)]
    if script.lesson:
        out.append(("lesson", script.lesson))
    return out


def _norm(word: str) -> str:
    return re.sub(r"\W", "", word.lower())


def _matches(said: Sequence[str], heard: Sequence[Word]) -> list[tuple[float, float] | None]:
    times: list[tuple[float, float] | None] = [None] * len(said)
    matcher = difflib.SequenceMatcher(
        a=[_norm(w) for w in said], b=[_norm(w.text) for w in heard], autojunk=False
    )
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            h = heard[block.b + k]
            times[block.a + k] = (h.start, h.end)
    return times


def _gap(times: list[tuple[float, float] | None], i: int, j: int, heard: Sequence[Word]) -> None:
    """Spread times[i:j] (all unmatched) between their matched neighbours."""
    count = j - i
    before, after = times[i - 1] if i else None, times[j] if j < len(times) else None
    if before and after:
        lo, hi = before[1], after[0]
    elif after:
        lo, hi = max(0.0, after[0] - GUESS_WORD_S * count), after[0]
    elif before:
        lo = before[1]
        hi = max(heard[-1].end, lo + GUESS_WORD_S * count)
    else:
        lo, hi = heard[0].start, heard[-1].end
    step = max(hi - lo, 0.0) / count
    for k in range(count):
        times[i + k] = (lo + step * k, lo + step * (k + 1))


def align(said: Sequence[str], heard: Sequence[Word]) -> tuple[tuple[Word, ...], float]:
    """The script's words with times from the take, and the share that matched."""
    if not heard:
        raise CaptionsError("the transcription found no words in the take")
    if not said:
        raise CaptionsError("the script has no words")
    times = _matches(said, heard)
    matched = sum(t is not None for t in times) / len(said)
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        _gap(times, i, j, heard)
        i = j
    words = tuple(Word(w, *t) for w, t in zip(said, times, strict=True) if t is not None)
    return words, matched


def _ends_clause(word: str) -> bool:
    return word.rstrip(_QUOTES)[-1:] in ".!?;:,"


def _breaks(group: list[Word], nxt: Word) -> bool:
    last = group[-1]
    text = " ".join(w.text for w in [*group, nxt])
    return (
        len(group) >= MAX_WORDS
        or len(text) > MAX_CHARS
        or _ends_clause(last.text)
        or nxt.start - last.end > PAUSE_S
    )


def cues(words: Sequence[Word]) -> tuple[Cue, ...]:
    """Short captions: a few words each, split at punctuation and pauses. A caption
    stays up until the next one unless a pause comes first."""
    groups: list[list[Word]] = []
    for w in words:
        if groups and not _breaks(groups[-1], w):
            groups[-1].append(w)
        else:
            groups.append([w])
    out: list[Cue] = []
    for n, g in enumerate(groups):
        end = g[-1].end
        if n + 1 < len(groups) and groups[n + 1][0].start - end <= PAUSE_S:
            end = groups[n + 1][0].start
        text = " ".join(w.text for w in g).rstrip(_QUOTES).rstrip(_HOLD).strip(_QUOTES)
        out.append(Cue(text, g[0].start, max(end, g[0].start)))
    return tuple(out)


def sections(named: Sequence[tuple[str, str]], words: Sequence[Word]) -> tuple[Section, ...]:
    """When each spoken part starts and ends, from the aligned words."""
    out: list[Section] = []
    at = 0
    for name, text in named:
        n = len(text.split())
        if n == 0 or at + n > len(words):
            raise CaptionsError(f"the timed words do not cover the part {name!r}")
        out.append(Section(name, words[at].start, words[at + n - 1].end))
        at += n
    return tuple(out)


def build(video_id: str, script: Script, heard: Sequence[Word]) -> Captions:
    named = parts(script)
    said = [w for _, text in named for w in text.split()]
    words, matched = align(said, heard)
    return Captions(video_id, words, cues(words), sections(named, words), matched)


def to_json(c: Captions) -> str:
    def r(x: float) -> float:
        return round(x, 3)

    return json.dumps(
        {
            "video_id": c.video_id,
            "matched": round(c.matched, 3),
            "sections": [
                {"name": s.name, "start": r(s.start), "end": r(s.end)} for s in c.sections
            ],
            "cues": [{"text": q.text, "start": r(q.start), "end": r(q.end)} for q in c.cues],
            "words": [{"text": w.text, "start": r(w.start), "end": r(w.end)} for w in c.words],
        },
        ensure_ascii=False,
        indent=2,
    )


def from_json(raw: str) -> Captions:
    data = cast(dict[str, Any], json.loads(raw))

    def items(key: str) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], data[key])

    return Captions(
        video_id=data["video_id"],
        words=tuple(Word(w["text"], w["start"], w["end"]) for w in items("words")),
        cues=tuple(Cue(q["text"], q["start"], q["end"]) for q in items("cues")),
        sections=tuple(Section(s["name"], s["start"], s["end"]) for s in items("sections")),
        matched=data["matched"],
    )
