"""JSON logging to stderr with secrets redacted (CLAUDE.md, Secrets).

Log IDs, never API keys, Relay message bodies or answers. `redact` is the
backstop: it masks anything that looks like a key in a log line's message
and extra fields, in case one slips through.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, TextIO, override

# Bearer tokens, OpenAI/Anthropic-style keys, and key=value pairs whose name says secret.
_TOKENS = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+|\b(?:sk|rly)[-_][A-Za-z0-9_-]{8,}")
_ASSIGNED = re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))(\s*[=:]\s*)\S+")
_SECRET_FIELD = re.compile(r"(?i)(key|token|secret|password|body|answer|text)$")
REDACTED = "[redacted]"

# Attributes every LogRecord has; anything else came from `extra=`.
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def redact(s: str) -> str:
    """Mask key-like substrings in s."""
    return _ASSIGNED.sub(r"\1\2" + REDACTED, _TOKENS.sub(REDACTED, s))


def _field(name: str, value: Any) -> Any:
    if _SECRET_FIELD.search(name):
        return REDACTED
    if isinstance(value, str | int | float | bool) or value is None:
        return redact(value) if isinstance(value, str) else value
    return redact(str(value))


class JSONFormatter(logging.Formatter):
    """One JSON object per line: time (UTC), level, logger, msg, extra fields."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for name, value in record.__dict__.items():
            if name not in _STANDARD and not name.startswith("_"):
                out[name] = _field(name, value)
        if record.exc_info:
            out["error"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


LEVELS = ("debug", "info", "warning", "error")


def setup(level: str = "info", stream: TextIO | None = None) -> None:
    """Send the reel_bot loggers' records to stream (stderr) as JSON lines."""
    if level not in LEVELS:
        raise ValueError(f"log level must be one of {', '.join(LEVELS)}")
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger("reel_bot")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False
