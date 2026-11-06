import base64
import json
from pathlib import Path
from typing import Any

import pytest

from reel_bot import cli_gemini_probe as probe
from reel_bot import visuals, web


class Net:
    def __init__(self, data: bytes = visuals.PNG_MAGIC) -> None:
        self.data = data
        self.requests: list[web.Request] = []

    def send(self, req: web.Request) -> web.Reply:
        self.requests.append(req)
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Here is the picture"},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(self.data).decode(),
                                }
                            },
                        ]
                    }
                }
            ]
        }
        return web.Reply(200, {}, json.dumps(payload).encode())

    def wait(self, seconds: float) -> None:
        pytest.fail("no retries")


def test_probe_sends_exactly_one_image_request_with_key_in_header() -> None:
    net = Net()
    assert probe.generate("fake-key", probe.MODELS[0], "A fox.", net) == visuals.PNG_MAGIC
    (request,) = net.requests
    assert request.url == f"{probe.API}/{probe.MODELS[0]}:generateContent"
    assert request.headers["x-goog-api-key"] == "fake-key"
    assert "fake-key" not in request.url
    assert request.retry is False
    assert json.loads(request.body)["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]


@pytest.mark.parametrize(
    "raw",
    [
        b"no JSON",
        b"null",
        b"[]",
        b"{}",
        b'{"candidates": []}',
        b'{"candidates": [{"content": {"parts": [1]}}]}',
    ],
)
def test_malformed_replies_raise_only_probe_error(raw: bytes) -> None:
    with pytest.raises(probe.ProbeError, match="valid image"):
        probe.parse(raw)


@pytest.mark.parametrize("data", ["***", "R0lGODlh", "", 17, "A" * (8 << 20)])
def test_bad_inline_images_are_refused(data: object) -> None:
    reply = {"candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}]}
    with pytest.raises(probe.ProbeError):
        probe.parse(json.dumps(reply).encode())


def test_text_only_reply_is_not_an_image() -> None:
    reply = {"candidates": [{"content": {"parts": [{"text": "blocked"}]}}]}
    with pytest.raises(probe.ProbeError, match="no image"):
        probe.parse(json.dumps(reply).encode())


@pytest.mark.parametrize(
    ("key", "model", "prompt"),
    [
        ("", probe.MODELS[0], "fox"),
        ("k", "../other", "fox"),
        ("k", probe.MODELS[0], " "),
        ("k", probe.MODELS[0], "x" * 4001),
    ],
)
def test_invalid_configuration_never_sends(key: str, model: str, prompt: str) -> None:
    net = Net()
    with pytest.raises(probe.ProbeError):
        probe.generate(key, model, prompt, net)
    assert not net.requests


def arguments(tmp_path: Path) -> list[str]:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("A fox.")
    return [
        "--model",
        probe.MODELS[0],
        "--prompt-file",
        str(prompt),
        "--out",
        str(tmp_path / "test"),
    ]


def test_cli_requires_billing_disabled_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        probe.main(arguments(tmp_path))


def test_cli_missing_key_and_existing_output_never_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    args = [*arguments(tmp_path), "--billing-disabled"]
    assert probe.main(args) == 1
    (tmp_path / "test.png").write_bytes(visuals.PNG_MAGIC)
    assert probe.main(args) == 1


def test_cli_saves_detected_image_and_handles_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_web(*args: Any) -> Net:
        return Net()

    monkeypatch.setattr(web, "Web", fake_web)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    args = [*arguments(tmp_path), "--billing-disabled"]
    assert probe.main(args) == 0
    assert (tmp_path / "test.png").read_bytes() == visuals.PNG_MAGIC
    (tmp_path / "test.png").unlink()

    def fail(*args: Any) -> bytes:
        raise web.WebError("gemini image probe failed: HTTP 429")

    monkeypatch.setattr(probe, "generate", fail)
    assert probe.main(args) == 1
