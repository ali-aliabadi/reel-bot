"""The morning pick: the Relay client, the question and its answer, and the run
against real SQLite with a fake Relay. Nothing here talks to Relay."""

import json
import random
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import (
    cli,
    cli_morning,
    morning,
    morning_write,
    relay_api,
    store,
    store_relay,
    store_videos,
    stories,
    videos,
)
from reel_bot.relay_api import Answer
from reel_bot.stories import Story
from reel_bot.web import Reply, Request

NOW = datetime(2026, 10, 22, 7, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
HARE = Story("kalilah-hare", "lion-and-hare", "The Lion and the Hare", "Kalilah", "Knatchbull", "1")
VIDEO = videos.Video("vid_01", NOW, "fable", "short")


class FakeNet:
    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.sent: list[Request] = []

    def send(self, req: Request) -> Reply:
        self.sent.append(req)
        return self.replies.pop(0)

    def wait(self, seconds: float) -> None:
        pass


def ok(body: object) -> Reply:
    return Reply(200, {}, json.dumps(body).encode())


# The Relay client


def test_send_adds_source_and_key_and_returns_the_id() -> None:
    net = FakeNet(ok({"id": "msg_01ABC", "status": "queued"}))
    relay = relay_api.Client("https://relay.test", "rk_secret", "reel-bot", net)
    assert relay.send({"to": ["ali"], "text": "hi"}, "pick-vid_01") == "msg_01ABC"
    req = net.sent[0]
    assert (req.method, req.url) == ("POST", "https://relay.test/v1/messages")
    assert req.headers["Authorization"] == "Bearer rk_secret"
    body = json.loads(req.body)
    assert body["source"] == "reel-bot"
    assert body["idempotency_key"] == "pick-vid_01"


def test_answers_are_parsed_and_long_text_is_cut() -> None:
    net = FakeNet(ok({"answers": [
        {"recipient": "ali", "answer": "x" * 5000, "answered_at": "2026-10-22T07:05:00Z"},
    ]}))  # fmt: skip
    relay = relay_api.Client("https://relay.test", "k", "reel-bot", net)
    (got,) = relay.answers("msg_01ABC")
    assert got.recipient == "ali"
    assert len(got.answer) == relay_api.MAX_ANSWER
    assert got.answered_at == NOW + timedelta(minutes=5)
    assert net.sent[0].url == "https://relay.test/v1/messages/msg_01ABC/answers"


@pytest.mark.parametrize("bad", ["msg_../x", "abc", "msg_01?a=1", ""])
def test_answers_refuse_odd_message_ids(bad: str) -> None:
    relay = relay_api.Client("https://relay.test", "k", "reel-bot", FakeNet())
    with pytest.raises(relay_api.RelayError, match="not a Relay message ID"):
        relay.answers(bad)


def test_answers_need_a_time_zone() -> None:
    raw = {"answers": [{"recipient": "ali", "answer": "a", "answered_at": "2026-10-22T07:05:00"}]}
    with pytest.raises(relay_api.RelayError):
        relay_api.parse_answers(json.dumps(raw).encode())


def test_web_errors_become_relay_errors() -> None:
    class Down(FakeNet):
        def send(self, req: Request) -> Reply:
            raise relay_api.web.WebError("Relay send failed: HTTP 401 (unauthorized)")

    relay = relay_api.Client("https://relay.test", "k", "reel-bot", Down())
    with pytest.raises(relay_api.RelayError, match="401"):
        relay.send({}, "k1")
    with pytest.raises(relay_api.RelayError, match="401"):
        relay.answers("msg_01")
    with pytest.raises(relay_api.RelayError, match="no message ID"):
        relay_api.parse_sent(b'{"id": 5}')


# The pure core


def test_settings_rotate_through_all_four_pairs() -> None:
    got = [morning.settings(n) for n in range(5)]
    assert got == [
        ("fable", "short"), ("lesson", "short"), ("fable", "long"), ("lesson", "long"),
        ("fable", "short"),
    ]  # fmt: skip


def test_labels_are_numbered_and_fit_a_button() -> None:
    long = Story("x-long", "long", "A" * 100, "Rumi", "Whinfield", "1")
    assert morning.labels([FOX, long]) == ["1. The Fox and the Grapes", "2. " + "A" * 61]


def test_question_offers_the_stories_and_a_skip() -> None:
    body = morning.question("ali", VIDEO, [FOX, HARE])
    assert body["to"] == ["ali"]
    assert body["urgency"] == "normal"
    blocks = body["blocks"]
    assert isinstance(blocks, list)
    assert blocks[-1] == {
        "type": "question",
        "text": "Which fable should Tara tell next?",
        "options": ["1. The Fox and the Grapes", "2. The Lion and the Hare", "Skip today"],
    }
    assert (
        blocks[0]["text"] == "1. The Fox and the Grapes (Aesop)\n2. The Lion and the Hare (Kalilah)"
    )


def answer(text: str, minutes: int = 5, who: str = "ali") -> Answer:
    return Answer(who, text, NOW + timedelta(minutes=minutes))


def test_decide() -> None:
    options = [FOX, HARE]
    assert morning.decide([], options) is None
    assert morning.decide([answer("2. The Lion and the Hare")], options) == morning.Decision(
        "picked", "kalilah-hare", "ali"
    )
    assert morning.decide([answer("Skip today")], options) == morning.Decision("skipped", by="ali")
    typed = morning.decide([answer("2. The Lion and the Hare please")], options)
    assert typed == morning.Decision("unreadable", by="ali")
    first = [answer("2. The Lion and the Hare", 9, "tara"), answer("1. The Fox and the Grapes", 3)]
    assert morning.decide(first, options) == morning.Decision("picked", "aesop-fox", "ali")


# The run, on real SQLite with a fake Relay


class FakeRelay:
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, object], str]] = []
        self.replies: dict[str, list[Answer]] = {}

    def send(self, body: dict[str, object], key: str) -> str:
        self.sent.append((body, key))
        return f"msg_{len(self.sent):02d}"

    def answers(self, message_id: str) -> tuple[Answer, ...]:
        return tuple(self.replies.get(message_id, []))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = store.open_db(tmp_path / "db.sqlite")
    store.sync_stories(c, stories.load_builtin())
    yield c
    c.close()


@pytest.fixture
def relay() -> FakeRelay:
    return FakeRelay()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def ctx(relay: FakeRelay, clock: Clock) -> morning_write.Context:
    return morning_write.Context(relay, "ali", clock, random.Random(1))


def asked(conn: sqlite3.Connection) -> videos.Video:
    (m,) = store_relay.open_messages(conn, morning.PURPOSE)
    return store_videos.get_video(conn, m.video_id)


def tap(relay: FakeRelay, conn: sqlite3.Connection, n: int) -> str:
    """Tap button n (1-based) on the open question; returns the story's id."""
    (m,) = store_relay.open_messages(conn, morning.PURPOSE)
    video = store_videos.get_video(conn, m.video_id)
    by_id = {s.id: s for s in store.all_stories(conn)}
    label = morning.labels([by_id[i] for i in video.story_options])[n - 1]
    relay.replies[m.message_id] = [answer(label)]
    return video.story_options[n - 1]


def test_ask_wait_then_record_the_pick(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    line = morning_write.run(conn, c)
    video = asked(conn)
    assert line == f"{video.id}: asked ali to pick (fable, short)"
    assert len(video.story_options) == 3
    assert len({s.tale for s in store.all_stories(conn) if s.id in video.story_options}) == 3
    assert relay.sent[0][1] == f"pick-{video.id}"
    assert morning_write.run(conn, c) == f"{video.id}: waiting for the pick"

    story_id = tap(relay, conn, 2)
    assert morning_write.run(conn, c) == f"{video.id}: picked {story_id} by ali"
    picked = store_videos.get_video(conn, video.id)
    assert (picked.story_id, picked.chosen_by, picked.chosen_at) == (story_id, "ali", NOW)
    assert store_relay.open_messages(conn, morning.PURPOSE) == []
    assert morning_write.run(conn, c) == f"{video.id}: already asked today"
    assert len(relay.sent) == 1


def test_next_day_asks_again_with_the_next_settings(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    morning_write.run(conn, c)
    first = tap(relay, conn, 1)
    morning_write.run(conn, c)
    clock.now += timedelta(days=1)
    line = morning_write.run(conn, c)
    assert line.endswith("asked ali to pick (lesson, short)")
    used = {s.tale for s in store.all_stories(conn) if s.id == first}
    offered = asked(conn).story_options
    assert not used & {s.tale for s in store.all_stories(conn) if s.id in offered}


def test_skip_closes_without_a_pick(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    morning_write.run(conn, c)
    video = asked(conn)
    relay.replies["msg_01"] = [answer(morning.SKIP)]
    assert morning_write.run(conn, c) == f"{video.id}: skipped"
    assert store_videos.get_video(conn, video.id).story_id is None
    assert morning_write.run(conn, c).endswith("already asked today")
    clock.now += timedelta(days=1)
    assert morning_write.run(conn, c).endswith(
        "(fable, short)"
    )  # a skip doesn't shift the rotation


def test_unanswered_question_expires(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    morning_write.run(conn, c)
    old = asked(conn)
    clock.now += morning.EXPIRE_AFTER + timedelta(hours=4)
    morning_write.run(conn, c)
    assert asked(conn).id != old.id
    assert [k for _, k in relay.sent] == [f"pick-{old.id}", f"pick-{asked(conn).id}"]


def test_typed_reply_is_unreadable(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    morning_write.run(conn, c)
    video = asked(conn)
    relay.replies["msg_01"] = [answer("'; DROP TABLE videos; --")]
    assert morning_write.run(conn, c) == f"{video.id}: unreadable"
    assert store_videos.get_video(conn, video.id).story_id is None


def test_pick_of_a_tale_spent_meanwhile_is_refused(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    morning_write.run(conn, c)
    video = asked(conn)
    story_id = tap(relay, conn, 1)
    other = videos.Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, other)
    store_videos.choose_story(conn, other.id, story_id, "ali", NOW)  # by hand, meanwhile
    assert morning_write.run(conn, c) == f"{video.id}: refused {story_id} by ali"
    assert store_relay.open_messages(conn, morning.PURPOSE) == []


def test_a_pick_recorded_before_a_crash_closes_the_question(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    c = ctx(relay, clock)
    morning_write.run(conn, c)
    video = asked(conn)
    store_videos.choose_story(conn, video.id, video.story_options[0], "ali", NOW)
    assert morning_write.run(conn, c) == f"{video.id}: picked {video.story_options[0]}"


def test_a_run_that_stopped_after_sending_resends_with_the_same_key(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    class Lost(FakeRelay):
        def send(self, body: dict[str, object], key: str) -> str:
            super().send(body, key)
            raise relay_api.RelayError("Relay send failed: TimeoutError")

    lost = Lost()
    with pytest.raises(relay_api.RelayError):
        morning_write.run(conn, ctx(lost, clock))
    morning_write.run(conn, ctx(relay, clock))
    assert lost.sent[0][1] == relay.sent[0][1]  # the same video, so Relay sends once
    assert lost.sent[0][0] == relay.sent[0][0]


def test_an_empty_catalog_is_an_error(
    conn: sqlite3.Connection, relay: FakeRelay, clock: Clock
) -> None:
    for s in store.all_stories(conn):
        if s.tale not in store.used_tales(conn):
            v = videos.Video(videos.new_id(clock()), clock(), "fable", "short")
            store_videos.add_video(conn, v)
            store_videos.choose_story(conn, v.id, s.id, "ali", clock())
    with pytest.raises(morning.MorningError, match="every tale"):
        morning_write.run(conn, ctx(relay, clock))


# The command


def test_morning_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db.sqlite")}
    assert cli.main(["morning"], env=env) == cli_morning.EXIT_CONFIG
    assert "RELAY_URL" in capsys.readouterr().err

    env |= {"RELAY_URL": "https://relay.test", "RELAY_API_KEY": "rk_x", "RELAY_USER": "ali"}
    net = FakeNet(ok({"id": "msg_01", "status": "queued"}), Reply(200, {}, b"<html>"))
    monkeypatch.setattr(cli_morning.web, "Web", lambda *_a, **_k: net)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    assert cli.main(["morning"], env=env, clock=lambda: NOW) == 0
    assert "asked ali to pick" in capsys.readouterr().out
    assert cli.main(["morning", "--check"], env=env, clock=lambda: NOW) == cli_morning.EXIT_REFUSED
    assert "not readable" in capsys.readouterr().err


def test_check_only_never_asks(conn: sqlite3.Connection, relay: FakeRelay, clock: Clock) -> None:
    assert morning_write.run(conn, ctx(relay, clock), ask=False) == "no open question"
    assert relay.sent == []
    morning_write.run(conn, ctx(relay, clock))
    clock.now += morning.EXPIRE_AFTER + timedelta(minutes=1)  # e.g. 03:01 the next night
    assert morning_write.run(conn, ctx(relay, clock), ask=False) == "no open question"
    assert len(relay.sent) == 1  # the next question waits for the morning run
