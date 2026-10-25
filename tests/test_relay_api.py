import base64
import json
from pathlib import Path
from typing import Any, cast

import pytest

from reel_bot import relay_api, web
from reel_bot.relay_api import Answer, Relay, RelayError
from reel_bot.stories import Story

FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
LONG = Story("k-long", "long", "A" * 80, "Kalilah and Dimnah", "Keith-Falconer", "Book 1")


class FakeNet:
    def __init__(self, *bodies: bytes) -> None:
        self.bodies = list(bodies)
        self.sent: list[web.Request] = []

    def send(self, req: web.Request) -> web.Reply:
        self.sent.append(req)
        return web.Reply(200, {}, self.bodies.pop(0))

    def wait(self, seconds: float) -> None:
        raise AssertionError("no waiting")


def test_options_are_numbered_unique_and_within_relays_limit() -> None:
    labels = relay_api.options([FOX, LONG, FOX])
    assert labels[0] == "1. The Fox and the Grapes"
    assert len(labels[1]) == relay_api.MAX_OPTION
    assert labels[1].endswith("…")
    assert len(set(labels)) == 3


def test_pick_message_asks_with_one_button_per_story() -> None:
    m = relay_api.pick_message("ali", "vid_1", [FOX, LONG])
    assert m["to"] == ["ali"]
    assert m["idempotency_key"] == "vid_1-pick"
    blocks = m["blocks"]
    assert isinstance(blocks, list)
    assert blocks[1] == {
        "type": "question",
        "text": "Which story today?",
        "options": relay_api.options([FOX, LONG]),
    }


def test_take_message_asks_for_a_voice_note_and_refuses_an_overlong_script() -> None:
    m = relay_api.take_message("tara", "vid_1", FOX.title, "Once upon a time.")
    assert m["blocks"] == [
        {"type": "text", "text": "Once upon a time."},
        {
            "type": "question",
            "text": "Reply with a voice note of you reading it (or an audio file).",
            "reply": "voice",
        },
    ]
    with pytest.raises(RelayError, match="over Relay's"):
        relay_api.take_message("tara", "vid_1", FOX.title, "x" * 4001)


def test_picture_message_asks_for_one_picture_and_refuses_an_overlong_prompt() -> None:
    m = relay_api.picture_message("tara", "vid_1", FOX.title, ["A fox.", "A vine."], 2)
    assert m["to"] == ["tara"]
    assert m["title"] == f"Picture 2 of 2: {FOX.title}"
    assert m["idempotency_key"] == "vid_1-picture-2"
    text, question = cast(list[Any], m["blocks"])
    assert text == {"type": "text", "text": "A vine."}
    assert question["reply"] == "image"
    assert "ChatGPT" in question["text"]
    with pytest.raises(RelayError, match="picture 1's prompt"):
        relay_api.picture_message("tara", "vid_1", FOX.title, ["x" * 4001], 1)


def test_file_message_attaches_the_video_once_per_person(tmp_path: Path) -> None:
    mp4 = tmp_path / "render.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    m = relay_api.file_message(["tara", "ali", "tara"], "vid_1", FOX.title, mp4)
    assert m["to"] == ["tara", "ali"]
    f: Any = cast(list[Any], m["blocks"])[1]
    assert f["type"] == "file"
    assert f["content_type"] == "video/mp4"
    assert base64.b64decode(f["base64"]) == mp4.read_bytes()


def test_file_message_refuses_an_empty_or_huge_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mp4 = tmp_path / "render.mp4"
    mp4.write_bytes(b"")
    with pytest.raises(RelayError, match="1 byte to 45 MB"):
        relay_api.file_message(["tara"], "vid_1", FOX.title, mp4)
    mp4.write_bytes(b"x" * 11)
    monkeypatch.setattr(relay_api, "MAX_FILE_BYTES", 10)
    with pytest.raises(RelayError, match="1 byte to 45 MB"):
        relay_api.file_message(["tara"], "vid_1", FOX.title, mp4)


def test_alert_keys_differ_per_failure() -> None:
    a = relay_api.alert_message("ali", "vid_1", "the take", "boom", "2026-10-25T08:00:00")
    b = relay_api.alert_message("ali", "vid_1", "the take", "boom", "2026-10-25T08:05:00")
    assert a["urgency"] == "high"
    assert a["idempotency_key"] != b["idempotency_key"]


def test_parse_answers_reads_buttons_voice_notes_and_pictures() -> None:
    body = {
        "answers": [
            {"recipient": "ali", "answer": "1. The Fox", "answered_at": "x"},
            {
                "recipient": "tara",
                "answer": "",
                "audio": {"content_type": "audio/ogg; codecs=opus", "base64": "T2dnUw=="},
            },
            {
                "recipient": "tara",
                "answer": "",
                "image": {"content_type": "image/png", "base64": "iVBORw=="},
            },
        ]
    }
    ali, tara, picture = relay_api.parse_answers(json.dumps(body).encode())
    assert ali == Answer("ali", "1. The Fox", None, "")
    assert picture == Answer("tara", "", None, "", b"\x89PNG")
    assert tara.audio == b"OggS"
    assert tara.audio_type == "audio/ogg"
    assert tara.audio_suffix() == ".ogg"
    assert Answer("t", "", b"x", "audio/flac").audio_suffix() == ".audio"
    assert relay_api.parse_answers(b'{"answers": []}') == []


@pytest.mark.parametrize(
    "answer",
    [
        [],
        {"answer": "x"},
        {"recipient": "", "answer": "x"},
        {"recipient": "t", "answer": 3},
        {"recipient": "t", "audio": "T2dnUw=="},
        {"recipient": "t", "audio": {"content_type": "audio/ogg"}},
        {"recipient": "t", "audio": {"content_type": "audio/ogg", "base64": "not base64!"}},
        {"recipient": "t", "image": "iVBORw=="},
        {"recipient": "t", "image": {"content_type": "image/png", "base64": ""}},
    ],
)
def test_parse_answers_refuses_odd_answers(answer: object) -> None:
    with pytest.raises(RelayError):
        relay_api.parse_answers(json.dumps({"answers": [answer]}).encode())


def test_relay_sends_with_its_key_and_source_and_reads_answers() -> None:
    net = FakeNet(b'{"id": "msg_01A", "status": "queued"}', b'{"answers": []}')
    relay = Relay("https://relay.example.test", "rk_test", "reel-bot", net)
    assert relay.send({"to": ["ali"], "text": "hi"}) == "msg_01A"
    assert relay.answers("msg_01A") == []
    post, get = net.sent
    assert post.url == "https://relay.example.test/v1/messages"
    assert json.loads(post.body)["source"] == "reel-bot"
    assert post.headers["Authorization"] == "Bearer rk_test"
    assert get.url.endswith("/v1/messages/msg_01A/answers")
    assert get.headers["Authorization"] == "Bearer rk_test"


def test_relay_refuses_bad_ids() -> None:
    relay = Relay("https://relay.example.test", "k", "reel-bot", FakeNet(b'{"id": "nope"}'))
    with pytest.raises(RelayError, match="no message ID"):
        relay.send({"to": ["ali"], "text": "hi"})
    with pytest.raises(RelayError, match="not a Relay message ID"):
        relay.answers("msg_1/../../admin")
