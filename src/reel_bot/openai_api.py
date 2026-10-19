"""A small stdlib client for OpenAI's REST API (transcription and pictures).

One POST with a timeout and bounded retries on 429/5xx and network errors.
The key is sent only in the Authorization header and never logged; error
messages carry the status code, never the reply body.
"""

import logging
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlparse

log = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1"
ATTEMPTS = 3
_RETRY = {429, 500, 502, 503, 504}


class OpenAIError(Exception):
    pass


@dataclass(frozen=True)
class Client:
    api_key: str = field(repr=False)
    base_url: str = API_URL
    timeout_s: float = 120.0
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        url = urlparse(self.base_url)
        if url.scheme != "https" and url.hostname != "127.0.0.1":
            raise OpenAIError("the OpenAI URL must be https")
        if not self.api_key:
            raise OpenAIError("OPENAI_API_KEY is not set")

    def post(self, path: str, body: bytes, content_type: str, what: str) -> bytes:
        """POST body to base_url + path; `what` names the call in errors and logs."""
        req = urllib.request.Request(  # noqa: S310 (scheme checked in __post_init__)
            self.base_url.rstrip("/") + path,
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": content_type},
            method="POST",
        )
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                    return cast(bytes, resp.read())
            except urllib.error.HTTPError as err:
                if err.code not in _RETRY or attempt == ATTEMPTS:
                    raise OpenAIError(f"{what} failed: HTTP {err.code}") from err
                log.warning(f"{what} retry", extra={"status": err.code, "attempt": attempt})
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt == ATTEMPTS:
                    raise OpenAIError(f"{what} failed: {type(err).__name__}") from err
                log.warning(f"{what} retry", extra={"attempt": attempt})
            self.sleep(2.0**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


def form(fields: list[tuple[str, str]], filename: str, mime: str, data: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body with text fields and one file; returns (body, content type)."""
    boundary = "reelbot" + secrets.token_hex(12)
    out = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields
    )
    out += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    out += data + f"\r\n--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"
