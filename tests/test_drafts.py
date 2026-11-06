"""reel-bot's draft pictures (Workers AI's FLUX.1 schnell): Tara uses each draft or
makes her own, and a draft that can't be made falls back to her own picture."""

import base64
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from reel_bot import (
    config,
    daily,
    daily_run,
    drafts,
    flux,
    store,
    store_daily,
    store_videos,
    stories,
    visuals,
    web,
)
from reel_bot.relay_api import Answer
from test_daily import PNG, Calls, FakeRelay, make_run, picked_and_scripted, voiced

JPEG = b"\xff\xd8\xff\xe0 a fox"
ACCOUNT = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, stories.load_builtin())
    return c


class Painter:
    """A drafter that counts its calls; `broken` makes it fail like the network."""

    def __init__(self, data: bytes = JPEG) -> None:
        self.prompts: list[str] = []
        self.data = data
        self.broken = False

    def __call__(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        if self.broken:
            raise web.WebError("workers ai failed: HTTP 429")
        return self.data


def at_picture_one(
    conn: sqlite3.Connection, tmp_path: Path, painter: Painter
) -> tuple[daily_run.Run, FakeRelay, str]:
    """A video whose voice is in, with picture 1's question just sent (message 4)."""
    relay = FakeRelay()
    run = replace(make_run(conn, tmp_path / "out", relay, Calls()), draw=painter)
    vid = picked_and_scripted(run, relay)
    daily.tick(run)
    voiced(conn, vid)
    assert daily.tick(run) == [f"{vid}\tasked for picture 1"]
    return run, relay, vid


def test_tara_gets_the_draft_with_its_prompt_and_can_use_it(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    painter = Painter()
    run, relay, vid = at_picture_one(conn, tmp_path, painter)
    image, text, question = relay.last()["blocks"]
    assert base64.b64decode(image["base64"]) == JPEG
    assert image["content_type"] == "image/jpeg"
    assert text["type"] == "code"
    assert "A fox, scene 0." in text["text"] == painter.prompts[0]
    assert question["options"] == [drafts.USE, drafts.OWN]
    assert relay.last()["idempotency_key"] == f"{vid}-picture-1"

    relay.answer(4, Answer("tara", drafts.USE, None, ""))
    assert daily.tick(run) == [f"{vid}\tasked for picture 2"]
    folder = tmp_path / "out" / vid
    assert (folder / "picture-1.jpg").read_bytes() == JPEG
    (asset,) = [a for a in store_videos.assets(conn, vid) if a.path == "picture-1.jpg"]
    assert asset.source == "drawn by FLUX.1 schnell, chosen by tara"
    assert asset.licence == flux.LICENCE
    assert len(painter.prompts) == 2  # one draw per picture, never twice


def test_tara_can_make_her_own_instead(conn: sqlite3.Connection, tmp_path: Path) -> None:
    run, relay, vid = at_picture_one(conn, tmp_path, Painter())
    relay.answer(4, Answer("tara", drafts.OWN, None, ""))
    (line,) = daily.tick(run)
    assert line.startswith(f"{vid}\twaiting for pictures 1, 2, 3, 4")
    own = relay.last()
    assert own["idempotency_key"] == f"{vid}-picture-1-own"
    assert own["blocks"][1]["reply"] == "image"
    assert store_daily.steps(conn, vid)["picture-1"] == "msg_5"
    relay.answer(5, PNG)
    assert daily.tick(run) == [f"{vid}\tasked for picture 2"]
    (asset,) = [a for a in store_videos.assets(conn, vid) if a.path == "picture-1.png"]
    assert asset.source == "made in ChatGPT by tara"


def test_a_picture_sent_to_the_draft_question_is_hers(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    run, relay, vid = at_picture_one(conn, tmp_path, Painter())
    relay.answer(4, PNG)
    assert daily.tick(run) == [f"{vid}\tasked for picture 2"]
    assert (tmp_path / "out" / vid / "picture-1.png").is_file()


def test_a_gone_draft_is_asked_for_again(conn: sqlite3.Connection, tmp_path: Path) -> None:
    run, relay, vid = at_picture_one(conn, tmp_path, Painter())
    (tmp_path / "out" / vid / "draft-1.jpg").unlink()
    relay.answer(4, Answer("tara", drafts.USE, None, ""))
    daily.tick(run)
    assert "reel-bot's draft of it is gone" in relay.last()["blocks"][0]["text"]


def test_a_failed_draft_falls_back_to_the_usual_question(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    painter = Painter()
    painter.broken = True
    _, relay, _ = at_picture_one(conn, tmp_path, painter)
    assert relay.last()["blocks"][1]["reply"] == "image"


@pytest.mark.parametrize("data", [b"RIFF\0\0\0\0WEBP", b"GIF89a"])
def test_drafts_relay_cant_show_are_skipped(tmp_path: Path, data: bytes) -> None:
    assert drafts.make(tmp_path, 1, "A fox.", Painter(data)) is None
    assert drafts.found(tmp_path, 1) is None


def test_a_draft_on_disk_is_reused(tmp_path: Path) -> None:
    painter = Painter(visuals.PNG_MAGIC)
    first = drafts.make(tmp_path, 2, "A fox.", painter)
    assert first == tmp_path / "draft-2.png"
    assert drafts.make(tmp_path, 2, "A fox.", None) == first
    assert len(painter.prompts) == 1


class FakeNet:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.sent: list[web.Request] = []

    def send(self, req: web.Request) -> web.Reply:
        self.sent.append(req)
        return web.Reply(200, {}, self.body)

    def wait(self, seconds: float) -> None:
        pass


def test_flux_asks_workers_ai_for_one_picture() -> None:
    reply = {"result": {"image": base64.b64encode(JPEG).decode()}, "success": True}
    net = FakeNet(json.dumps(reply).encode())
    assert flux.Flux(ACCOUNT, "tok", net).draw("p" * 5000) == JPEG
    (req,) = net.sent
    assert req.url == f"{flux.API}/accounts/{ACCOUNT}/ai/run/{flux.MODEL}"
    assert req.headers["Authorization"] == "Bearer tok"
    body = json.loads(req.body)
    assert body == {"prompt": "p" * flux.MAX_PROMPT, "steps": flux.STEPS}
    assert "tok" not in repr(flux.Flux(ACCOUNT, "tok", net))


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (b'{"result": {"image": "R0lGODlh"}}', "not a PNG, JPEG or WebP"),
        (b'{"result": {"image": "***"}}', "not base64"),
        (b'{"result": {}}', "no picture"),
        (json.dumps({"result": {"image": "A" * (8 << 20)}}).encode(), "over 5 MB"),
    ],
)
def test_odd_workers_ai_replies_are_refused(body: bytes, problem: str) -> None:
    with pytest.raises(flux.FluxError, match=problem):
        flux.parse(body)


def test_drafts_are_off_unless_cloudflare_is_set_up() -> None:
    net = FakeNet(b"")
    assert flux.from_env({}, net) is None
    found = flux.from_env(
        {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT.upper(), "CLOUDFLARE_API_TOKEN": "t"}, net
    )
    assert found is not None
    assert found.account == ACCOUNT
    with pytest.raises(config.ConfigError, match="both"):
        flux.from_env({"CLOUDFLARE_API_TOKEN": "t"}, net)
    with pytest.raises(config.ConfigError, match="32 hex"):
        flux.from_env({"CLOUDFLARE_ACCOUNT_ID": "../x", "CLOUDFLARE_API_TOKEN": "t"}, net)


def test_a_typed_answer_to_the_draft_is_asked_for_again(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    run, relay, _ = at_picture_one(conn, tmp_path, Painter())
    relay.answer(4, Answer("tara", "hmm", None, ""))
    daily.tick(run)
    assert "the answer has no picture" in relay.last()["blocks"][0]["text"]
