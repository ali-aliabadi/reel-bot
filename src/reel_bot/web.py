"""Small stdlib HTTP for the posting platforms: one request with a timeout and
bounded retries on 429/5xx and network errors.

Tokens travel only in headers, bodies or query strings and are never logged;
errors carry the status code and at most the platform's short error code, never
the URL or the reply body.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ATTEMPTS = 3
_RETRY = {429, 500, 502, 503, 504}
_CODE = re.compile(r"^[A-Za-z_]{1,80}$")


class WebError(Exception):
    pass


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    what: str  # names the call in errors and logs
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    retry: bool = True  # False for calls that create a post, so a lost reply never posts twice


@dataclass(frozen=True)
class Reply:
    status: int
    headers: Mapping[str, str]  # names in lower case
    body: bytes


class Net(Protocol):
    """What the platform clients need: send a request, and wait between polls."""

    def send(self, req: Request) -> Reply: ...

    def wait(self, seconds: float) -> None: ...


def _hint(body: bytes) -> str:
    """The platform's short error code from an error reply (e.g. invalid_grant), or ''."""
    try:
        data: object = json.loads(body)
    except (ValueError, RecursionError):
        return ""
    err = cast(dict[str, object], data).get("error") if isinstance(data, dict) else None
    found: list[object] = [err] if isinstance(err, str) else []
    if isinstance(err, dict):
        e = cast(dict[str, object], err)
        found = [e.get("code"), e.get("status"), e.get("type")]
    codes = [c for c in found if isinstance(c, str) and _CODE.match(c)]
    return f" ({codes[0]})" if codes else ""


@dataclass(frozen=True)
class Web:
    """The real Net: urllib with a timeout per attempt."""

    timeout_s: float = 300.0
    sleep: Callable[[float], None] = time.sleep

    def wait(self, seconds: float) -> None:
        self.sleep(seconds)

    def send(self, req: Request) -> Reply:
        what = req.what
        parsed = urlparse(req.url)
        if parsed.scheme != "https" and parsed.hostname != "127.0.0.1":
            raise WebError(f"{what}: only https URLs")
        r = urllib.request.Request(  # noqa: S310 (scheme checked above)
            req.url, data=req.body or None, headers=dict(req.headers), method=req.method
        )
        attempts = ATTEMPTS if req.retry else 1
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(r, timeout=self.timeout_s) as resp:  # noqa: S310
                    found = {k.lower(): v for k, v in resp.headers.items()}
                    return Reply(resp.status, found, cast(bytes, resp.read()))
            except urllib.error.HTTPError as err:
                if err.code not in _RETRY or attempt == attempts:
                    raise WebError(f"{what} failed: HTTP {err.code}{_hint(err.read())}") from err
                log.warning(f"{what} retry", extra={"status": err.code, "attempt": attempt})
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt == attempts:
                    raise WebError(f"{what} failed: {type(err).__name__}") from err
                log.warning(f"{what} retry", extra={"attempt": attempt})
            self.sleep(2.0**attempt)
        raise AssertionError("unreachable")  # pragma: no cover
