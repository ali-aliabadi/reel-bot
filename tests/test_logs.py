import io
import json
import logging

import pytest

from reel_bot import logs


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ("Authorization: Bearer rly_abc123def456", "abc123def456"),
        ("calling openai with sk-proj-ABCDEFGHijkl", "ABCDEFGHijkl"),
        ("RELAY_API_KEY=supersecretvalue", "supersecretvalue"),
        ("openai_api_key: hunter2hunter2", "hunter2hunter2"),
    ],
)
def test_redact_masks_keys(raw: str, leak: str) -> None:
    out = logs.redact(raw)
    assert leak not in out
    assert logs.REDACTED in out


def test_redact_keeps_ids() -> None:
    line = "sent msg_01JABCDEF for vid_01JXYZ (story panchatantra-monkey-and-crocodile)"
    assert logs.redact(line) == line


def _logged(**extra: object) -> dict[str, object]:
    buf = io.StringIO()
    logs.setup("debug", buf)
    logging.getLogger("reel_bot.test").info("asked tara for %s", "vid_1", extra=extra)
    return json.loads(buf.getvalue())


def test_json_line_has_fields_and_redacts_secret_extras() -> None:
    got = _logged(video_id="vid_1", answer="tara's private words", api_key="k", attempt=2)
    assert got["level"] == "info"
    assert got["logger"] == "reel_bot.test"
    assert got["msg"] == "asked tara for vid_1"
    assert str(got["time"]).endswith("+00:00")
    assert got["video_id"] == "vid_1"
    assert got["attempt"] == 2
    assert got["answer"] == logs.REDACTED
    assert got["api_key"] == logs.REDACTED


def test_exception_is_logged_redacted() -> None:
    buf = io.StringIO()
    logs.setup("info", buf)
    try:
        raise RuntimeError("relay said no to Bearer rly_secretsecret")  # noqa: TRY301
    except RuntimeError:
        logging.getLogger("reel_bot.test").exception("send failed")
    got = json.loads(buf.getvalue())
    assert "RuntimeError" in got["error"]
    assert "rly_secretsecret" not in got["error"]


def test_level_filters() -> None:
    buf = io.StringIO()
    logs.setup("warning", buf)
    logging.getLogger("reel_bot.test").info("quiet")
    assert buf.getvalue() == ""


def test_unknown_level_is_refused() -> None:
    with pytest.raises(ValueError, match="log level"):
        logs.setup("loud")
