import json
import random
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reel_bot import (
    daily,
    pipeline,
    script,
    store,
    store_daily,
    store_videos,
    stories,
    videos,
    visuals,
    web,
)
from reel_bot.captions import Word
from reel_bot.captions_write import Heard
from reel_bot.relay_api import Answer, RelayError
from reel_bot.script_write import Reply

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
SCRIPT = json.dumps(
    {
        "hook": "A hungry fox found grapes he could not reach.",
        "paragraphs": [
            {"text": " ".join([f"word{i}"] * 25), "picture": f"A fox, scene {i}."} for i in range(4)
        ],
        "lesson": "",
    }
)
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; the full daily run needs them",
)


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


class FakeRelay:
    """Records what reel-bot sends; `answer` plays a person replying."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replies: dict[str, list[Answer]] = {}
        self.down = False
        self.refuse = False  # Relay up, but refusing what is sent
        self.limit = 1000  # refuse sends after this many

    def send(self, message: dict[str, object]) -> str:
        if self.refuse or len(self.sent) >= self.limit:
            raise RelayError("refused")
        if self.down:
            raise web.WebError("relay send failed: URLError")
        self.sent.append(message)
        return f"msg_{len(self.sent)}"

    def answers(self, message_id: str) -> list[Answer]:
        if self.down:
            raise web.WebError("relay answers failed: URLError")
        return self.replies.get(message_id, [])

    def answer(self, n: int, got: Answer) -> None:
        self.replies[f"msg_{n}"] = [got]

    def last(self) -> dict[str, Any]:
        return self.sent[-1]


class Calls:
    def claude(self, system: str, user: str, schema: dict[str, Any]) -> Reply:
        return Reply(SCRIPT, "claude-opus-5-5", 1000, 1000)

    def transcribe(self, audio: Path) -> Heard:
        said = script.from_json((audio.parent / "script.json").read_text()).spoken().split()
        step = 5.0 / len(said)
        return Heard(tuple(Word(w, i * step, (i + 1) * step) for i, w in enumerate(said)), 6.0)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, stories.load_builtin())
    return c


def make_run(conn: sqlite3.Connection, out: Path, relay: FakeRelay, calls: Calls) -> daily.Run:
    clock = Clock()

    def context(vid: str) -> pipeline.Context:
        return pipeline.Context(out, clock, lambda: calls.claude, lambda: calls.transcribe)

    return daily.Run(conn, relay, daily.People("ali", "tara"), clock, context)


def button(relay: FakeRelay, n: int = 1, choice: int = 0) -> Answer:
    options = relay.sent[n - 1]["blocks"][1]["options"]
    return Answer("ali", options[choice], None, "")


def pasted(text: str = SCRIPT) -> Answer:
    return Answer("tara", f"Here is your script:\n```json\n{text}\n```", None, "")


def voiced(conn: sqlite3.Connection, vid: str) -> None:
    """Record Tara's take as imported, without ffmpeg."""
    store_videos.record_stage(conn, vid, videos.StageRun("voice", "voice.wav", 0, 1, NOW))


PNG = Answer("tara", "", None, "", visuals.PNG_MAGIC + b"rest")


def picked_and_scripted(run: daily.Run, relay: FakeRelay) -> str:
    """Start a video, pick its story, paste the script back: Tara is asked next."""
    vid = str(daily.start(run, "fable", "short", random.Random(1)))
    relay.answer(1, button(relay))
    daily.tick(run)
    relay.answer(2, pasted())
    return vid


def test_start_asks_ali_once_until_the_pick_is_answered(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = daily.start(run, "fable", "short", random.Random(1))
    assert vid is not None
    assert relay.last()["to"] == ["ali"]
    assert len(relay.last()["blocks"][1]["options"]) == daily.CHOICES
    assert len(store_videos.get_video(conn, vid).story_options) == daily.CHOICES
    assert store_daily.steps(conn, vid) == {"pick": "msg_1"}
    assert daily.start(run, "fable", "short", random.Random(1)) is None
    assert len(relay.sent) == 1


def test_start_refuses_when_every_tale_is_told(tmp_path: Path) -> None:
    conn = store.open_db(tmp_path / "empty.db")
    run = make_run(conn, tmp_path / "out", FakeRelay(), Calls())
    with pytest.raises(daily.DailyError, match="every tale"):
        daily.start(run, "fable", "short", random.Random(1))


def test_tick_records_the_pick_then_asks_tara_once(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = daily.start(run, "fable", "short", random.Random(1))
    assert vid is not None
    assert daily.tick(run) == [f"{vid}\twaiting for the pick"]

    relay.answer(1, button(relay, choice=2))
    assert daily.tick(run) == [f"{vid}\tasked for the script"]
    video = store_videos.get_video(conn, vid)
    assert video.story_id == video.story_options[2]
    assert video.chosen_by == "ali"
    paste = relay.last()
    assert paste["to"] == ["tara"]
    assert paste["blocks"][1]["text"] == script.chat_prompt(
        next(s for s in store.all_stories(conn) if s.id == video.story_id), video
    )
    assert "options" not in paste["blocks"][2]
    assert daily.tick(run) == [f"{vid}\twaiting for the script"]

    relay.answer(2, pasted())
    assert daily.tick(run) == [f"{vid}\tasked for the take"]
    assert store_videos.stage_runs(conn, vid)["script"].cost_usd == 0
    ask = relay.last()
    assert ask["to"] == ["tara"]
    assert ask["blocks"][0]["text"].startswith("A hungry fox")
    assert ask["blocks"][1]["reply"] == "voice"
    (line,) = daily.tick(run)
    assert line.startswith(f"{vid}\twaiting for Tara's take of ")
    assert len(relay.sent) == 3  # one step at a time: no picture before the take


def test_tara_gets_one_picture_at_a_time(conn: sqlite3.Connection, tmp_path: Path) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = picked_and_scripted(run, relay)
    daily.tick(run)
    voiced(conn, vid)
    assert daily.tick(run) == [f"{vid}\tasked for picture 1"]
    (line,) = daily.tick(run)
    assert line.startswith(f"{vid}\twaiting for pictures 1, 2, 3, 4 ")
    relay.answer(4, PNG)
    assert daily.tick(run) == [f"{vid}\tasked for picture 2"]
    picture = relay.last()
    assert picture["idempotency_key"] == f"{vid}-picture-2"
    assert picture["blocks"][1]["reply"] == "image"
    assert "A fox, scene 1." in picture["blocks"][0]["text"]
    assert len(relay.sent) == 5


def test_a_script_with_problems_goes_back_to_chatgpt_twice_then_fails(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = str(daily.start(run, "fable", "short", random.Random(1)))
    relay.answer(1, button(relay))
    daily.tick(run)
    relay.answer(2, Answer("tara", "Sorry, I can't help with that.", None, ""))
    assert daily.tick(run) == [f"{vid}\tasked for fix 1 of the script"]
    fix = relay.last()
    assert fix["idempotency_key"] == f"{vid}-script-2"
    assert "it is not the JSON asked for" in fix["blocks"][1]["text"]
    relay.answer(3, pasted(SCRIPT.replace("word0", "word0 extra")))
    assert daily.tick(run) == [f"{vid}\tasked for fix 2 of the script"]
    relay.answer(4, pasted(json.dumps({"hook": "", "paragraphs": [], "lesson": ""})))
    (line,) = daily.tick(run)
    assert line.startswith(f"{vid}\tfailed: ChatGPT's script still has problems: it has 0 words")
    assert {"label": "Step", "value": "script"} in relay.last()["blocks"][0]["items"]
    assert "script" not in store_videos.stage_runs(conn, vid)


def test_relay_down_while_waiting_is_not_a_failure(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = daily.start(run, "fable", "short", random.Random(1))
    relay.down = True
    assert daily.tick(run) == [f"{vid}\twaiting for the pick"]
    assert "failed" not in store_daily.steps(conn, str(vid))


def test_a_pick_that_was_not_offered_fails_alerts_ali_and_can_be_retried(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = str(daily.start(run, "fable", "short", random.Random(1)))
    relay.answer(1, Answer("ali", "4. Something else", None, ""))
    (line,) = daily.tick(run)
    assert line == f"{vid}\tfailed: the pick is not one of the offered stories"
    alert = relay.last()
    assert alert["urgency"] == "high"
    assert alert["to"] == ["ali"]
    assert {"label": "Step", "value": "story pick"} in alert["blocks"][0]["items"]
    assert daily.tick(run) == []  # a failed video waits for a retry

    daily.retry(conn, vid)
    relay.answer(1, button(relay))
    assert daily.tick(run) == [f"{vid}\tasked for the script"]
    with pytest.raises(daily.DailyError, match="has not failed"):
        daily.retry(conn, vid)


def test_a_take_without_audio_fails_even_when_the_alert_cannot_be_sent(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = picked_and_scripted(run, relay)
    daily.tick(run)
    relay.answer(3, Answer("tara", "I'll do it later", None, ""))
    relay.refuse = True
    (line,) = daily.tick(run)
    assert line == f"{vid}\tfailed: the take's answer has no audio"
    assert store_daily.steps(conn, vid)["failed"] == "unsent"


def test_an_answer_without_a_picture_fails(conn: sqlite3.Connection, tmp_path: Path) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = picked_and_scripted(run, relay)
    daily.tick(run)
    voiced(conn, vid)
    daily.tick(run)
    relay.answer(4, Answer("tara", "", b"OggS", "audio/ogg"))
    assert daily.tick(run) == [f"{vid}\tfailed: the answer to picture 1 has no picture"]
    assert {"label": "Step", "value": "the take and pictures"} in relay.last()["blocks"][0]["items"]


def test_asking_tara_resumes_where_relay_stopped(conn: sqlite3.Connection, tmp_path: Path) -> None:
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = picked_and_scripted(run, relay)
    daily.tick(run)
    voiced(conn, vid)
    relay.limit = 3  # the pick, the script and the take went out; picture 1 is refused
    assert daily.tick(run) == [f"{vid}\tfailed: refused"]
    daily.retry(conn, vid)
    relay.limit = 1000
    assert daily.tick(run) == [f"{vid}\tasked for picture 1"]
    assert relay.last()["idempotency_key"] == f"{vid}-picture-1"


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # noqa: S603, S607


@needs_ffmpeg
def test_a_voice_note_becomes_a_video_sent_to_tara_and_ali(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    png, take = tmp_path / "p.png", tmp_path / "take.ogg"
    _ffmpeg("-f", "lavfi", "-i", "color=c=teal:s=1024x1536", "-frames:v", "1", str(png))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=200:duration=6", "-c:a", "libopus", str(take))
    relay = FakeRelay()
    run = make_run(conn, tmp_path / "out", relay, Calls())
    vid = picked_and_scripted(run, relay)
    daily.tick(run)
    relay.answer(3, Answer("tara", "", take.read_bytes(), "audio/ogg"))
    assert daily.tick(run) == [f"{vid}\tasked for picture 1"]
    for n in (4, 5, 6):
        relay.answer(n, Answer("tara", "", None, "", png.read_bytes()))
        assert daily.tick(run) == [f"{vid}\tasked for picture {n - 2}"]
    relay.answer(7, Answer("tara", "", None, "", png.read_bytes()))

    (line,) = daily.tick(run)
    assert line.startswith(f"{vid}\tsent ")
    sent = relay.last()
    assert sent["to"] == ["tara", "ali"]
    assert sent["blocks"][1]["filename"] == f"{vid}.mp4"
    assert (tmp_path / "out" / vid / "take.ogg").read_bytes() == take.read_bytes()
    assert daily.tick(run) == []  # done: nothing is sent twice
