"""Odd inputs for everything that comes from outside: model replies, API
replies and timings. Each parser may only raise its own error; the timing code
must keep its promises (every word timed, times in order) for any input.
Seeded, so a failure always reproduces."""

import contextlib
import itertools
import json
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reel_bot import (
    captions,
    captions_write,
    instagram,
    publish,
    relay_api,
    render,
    script,
    tiktok,
    visuals_write,
    youtube,
)
from reel_bot.captions import Word
from reel_bot.publish import Post, PublishError
from reel_bot.stories import Story
from reel_bot.videos import Video
from reel_bot.visuals import VisualsError
from reel_bot.web import Reply, Request

CASES = 400
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
VIDEO = Video("vid_X", datetime(2026, 10, 22, tzinfo=UTC), "fable", "short")
ODD_VALUES: list[Any] = [
    None, True, 0, -1, 1e308, float("nan"), "", "x" * 10_000, "nan", "-inf", [], {},
    [None], {"a": None}, "\u0000", "\U0001f98a", "{", "\\",
]  # fmt: skip


def odd(rng: random.Random, depth: int = 0) -> Any:
    """A random JSON-like value, sometimes shaped like the real thing."""
    pick = rng.random()
    if depth > 3 or pick < 0.4:
        return rng.choice(ODD_VALUES)
    if pick < 0.7:
        return [odd(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    keys = ["hook", "paragraphs", "lesson", "text", "picture", "words", "word", "start",
            "end", "duration", "data", "b64_json", "answers", "recipient", "answer", "audio",
            "content_type", "base64", "id", rng.choice(["k", "é", ""])]  # fmt: skip
    return {rng.choice(keys): odd(rng, depth + 1) for _ in range(rng.randint(0, 5))}


def replies(rng: random.Random, good: dict[str, Any]) -> list[bytes]:
    """Odd replies around a good one: random JSON, a good reply with one field
    swapped for an odd value, cut-off JSON, random bytes and deep nesting."""
    swapped = dict(good)
    swapped[rng.choice(list(good))] = odd(rng)
    raw = json.dumps(good).encode()
    return [
        json.dumps(odd(rng)).encode(),
        json.dumps(swapped).encode(),
        raw[: rng.randint(0, len(raw))],
        rng.randbytes(rng.randint(0, 64)),
        b"[" * 100_000,
    ]


def parse_script(raw: bytes) -> object:
    return script.parse(raw.decode("utf-8", "replace"), FOX, VIDEO)


@pytest.mark.parametrize(
    ("parse", "error", "good"),
    [
        (
            parse_script,
            script.ScriptError,
            {"hook": "h", "paragraphs": [{"text": "t", "picture": "p"}], "lesson": ""},
        ),
        (
            captions_write.parse,
            captions.CaptionsError,
            {"duration": 3.0, "words": [["fox", 0.1, 0.4]]},
        ),
        (visuals_write.parse, VisualsError, {"data": [{"b64_json": "iVBORw0KGgo="}]}),
        (
            relay_api.parse_answers,
            relay_api.RelayError,
            {
                "answers": [
                    {
                        "recipient": "tara",
                        "answer": "",
                        "audio": {"content_type": "audio/ogg", "base64": "T2dnUw=="},
                    }
                ]
            },
        ),
        (relay_api.parse_sent, relay_api.RelayError, {"id": "msg_01J", "status": "queued"}),
    ],
    ids=["script", "transcription", "picture", "relay answers", "relay sent"],
)
def test_parsers_raise_only_their_own_error(
    parse: Callable[[bytes], object], error: type[Exception], good: dict[str, Any]
) -> None:
    rng = random.Random(7)
    parse(json.dumps(good).encode())  # the good reply parses
    for _ in range(CASES // 5):
        for raw in replies(rng, good):
            with contextlib.suppress(error):
                parse(raw)


def test_transcription_times_are_checked() -> None:
    bad = [["a", float("nan"), 1.0], ["a", -1.0, 1.0], ["a", 2.0, 1.0], ["a", 0.0, float("inf")]]
    for w in bad:
        with pytest.raises(captions.CaptionsError):
            captions_write.parse(json.dumps({"duration": 3.0, "words": [w]}).encode())


def heard_words(rng: random.Random, vocab: list[str]) -> list[Word]:
    t, out = rng.uniform(0, 2), list[Word]()
    for _ in range(rng.randint(1, 40)):
        start = t + rng.choice([0.0, 0.05, 0.5, 2.0])
        end = start + rng.choice([0.0, 0.1, 0.4])
        out.append(Word(rng.choice(vocab), start, end))
        t = end
    return out


def test_align_cues_and_timeline_keep_their_promises() -> None:
    rng = random.Random(11)
    vocab = ["the", "fox", "Fox,", "grapes.", "“Why?”", "it's", "--", "{x}", "\\n"]
    for _ in range(CASES):
        said = [rng.choice(vocab) for _ in range(rng.randint(1, 40))]
        heard = heard_words(rng, vocab)
        words, matched = captions.align(said, heard)
        assert [w.text for w in words] == said
        assert 0.0 <= matched <= 1.0
        assert all(0 <= w.start <= w.end for w in words)
        assert all(a.start <= b.start for a, b in itertools.pairwise(words))
        cues = captions.cues(words)
        assert all(q.start <= q.end for q in cues)
        assert sum(len(q.text.split()) for q in cues) <= len(words)
        ass = render.ass(cues)
        events = ass.split("[Events]")[1]
        assert "{" not in events
        assert "\\" not in events


def test_timeline_refuses_rather_than_making_empty_shots() -> None:
    rng = random.Random(13)
    for _ in range(CASES):
        names = ["hook", *(f"p{i}" for i in range(1, rng.randint(2, 6)))]
        t, sections = 0.0, list[captions.Section]()
        for n in names:
            start = t + rng.choice([0.0, 0.3])
            t = start + rng.choice([0.0, 0.5, 3.0])
            sections.append(captions.Section(n, start, t))
        cap = captions.Captions("vid_X", (), (), tuple(sections), 1.0)
        pictures = [(n, f"{n}.png") for n in names[1:]]
        try:
            shots = render.timeline(cap, pictures, t + rng.choice([-1.0, 0.0, 2.0]))
        except render.RenderError:
            continue
        assert all(s.seconds > 0 for s in shots)
        assert shots[0].start == 0


class OddNet:
    """Answers a platform flow with its good replies, one of them swapped for an odd one."""

    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = bodies

    def send(self, req: Request) -> Reply:
        body = self.bodies.pop(0) if self.bodies else b"{}"
        return Reply(200, {"location": "https://www.googleapis.com/u"}, body)

    def wait(self, seconds: float) -> None:
        pass


def _tt(data: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": "ok"}, "data": data}


YT = youtube.App("c", "s")
TT = tiktok.App("k", "s", "https://example.test/cb")
IG = {"access_token": "a", "user_id": "1", "saved_at": "2026-10-22T09:00:00+00:00"}
POST = Post("t", "d")
FLOWS: list[tuple[str, Callable[[OddNet, Path], object], list[dict[str, Any]]]] = [
    (
        "youtube",
        lambda n, mp4: youtube.upload(
            n, YT, youtube.access(n, YT, {"refresh_token": "r"})[0], mp4, POST
        ),
        [{"access_token": "a"}, {}, {"id": "abc"}],
    ),
    (
        "instagram",
        lambda n, mp4: (
            instagram.sign_in(n, "tok", VIDEO.created_at),
            instagram.access(n, IG, VIDEO.created_at + timedelta(days=2)),
            instagram.upload(n, "a", IG, mp4, POST),
        ),
        [
            {"user_id": "1"},
            {"access_token": "b"},
            {"id": "1"},
            {},
            {"status_code": "FINISHED"},
            {"id": "2"},
        ],
    ),
    (
        "tiktok",
        lambda n, mp4: (
            tiktok.access(n, TT, {"refresh_token": "r"}),
            tiktok.upload(n, TT, "a", mp4, POST),
        ),
        [
            {"access_token": "a", "refresh_token": "r"},
            _tt({"privacy_level_options": ["SELF_ONLY"]}),
            _tt({"publish_id": "p", "upload_url": "https://open-upload.tiktokapis.com/x"}),
            {},
            _tt({"status": "PUBLISH_COMPLETE"}),
        ],
    ),
]


@pytest.mark.parametrize(
    ("flow", "good"), [(f, g) for _, f, g in FLOWS], ids=[n for n, _, _ in FLOWS]
)
def test_platform_replies_raise_only_publish_errors(
    tmp_path: Path, flow: Callable[[OddNet, Path], object], good: list[dict[str, Any]]
) -> None:
    mp4 = tmp_path / "render.mp4"
    mp4.write_bytes(b"mp4")
    flow(OddNet([json.dumps(g).encode() for g in good]), mp4)  # the good replies post
    rng = random.Random(17)
    for _ in range(CASES // 10):
        i = rng.randrange(len(good))
        for odd_reply in replies(rng, good[i] or {"k": 1}):
            bodies = [json.dumps(g).encode() for g in good]
            bodies[i] = odd_reply
            with contextlib.suppress(PublishError):
                flow(OddNet(bodies), mp4)


def test_pasted_sign_in_addresses_raise_only_publish_errors() -> None:
    rng = random.Random(19)
    parts = ["http://", "[", "::1", "?", "state=s", "&", "code=", "%", "#", "\x00", "é", "error=x"]
    for _ in range(CASES):
        pasted = "".join(rng.choice(parts) for _ in range(rng.randint(0, 8)))
        with contextlib.suppress(PublishError):
            assert publish.code_from(pasted, "s")
