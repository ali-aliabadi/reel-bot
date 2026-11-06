"""Draft pictures from Cloudflare Workers AI (FLUX.1 schnell), free on its daily
allocation (10,000 neurons a day, about 100 pictures; reel-bot needs 3 to 6).

`parse` is pure; `Flux` sends one request with `web.Net` (timeout and bounded
retries). The token travels only in a header and is never logged; prompts and
pictures are not logged either. Off unless CLOUDFLARE_ACCOUNT_ID and
CLOUDFLARE_API_TOKEN are both set.
"""

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from reel_bot import config, visuals, web

MODEL = "@cf/black-forest-labs/flux-1-schnell"
API = "https://api.cloudflare.com/client/v4"
STEPS = 8  # the model's most, for the best picture; still far inside the free allocation
MAX_PROMPT = 2048  # the model's limit on the prompt
TIMEOUT_S = 90.0
MAX_BYTES = 5 << 20  # Relay's limit on an image block
LICENCE = "FLUX.1 [schnell], Apache-2.0, on Cloudflare Workers AI: commercial use allowed"
_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")


class FluxError(Exception):
    pass


def parse(body: bytes) -> bytes:
    """The picture in Workers AI's reply (`{"result": {"image": base64}}`) as PNG,
    JPEG or WebP bytes; anything else raises FluxError."""
    try:
        data: object = json.loads(body)
    except (ValueError, RecursionError) as err:
        raise FluxError("Workers AI's reply is not JSON") from err
    result = cast(dict[str, object], data).get("result") if isinstance(data, dict) else None
    image = cast(dict[str, object], result).get("image") if isinstance(result, dict) else None
    if not isinstance(image, str) or not image:
        raise FluxError("Workers AI's reply has no picture")
    if len(image) > MAX_BYTES * 4 // 3 + 4:
        raise FluxError("Workers AI's picture is over 5 MB")
    try:
        raw = base64.b64decode(image, validate=True)
    except (binascii.Error, ValueError) as err:
        raise FluxError("Workers AI's picture is not base64") from err
    try:
        visuals.suffix(raw)
    except visuals.VisualsError as err:
        raise FluxError(str(err)) from err
    return raw


@dataclass(frozen=True)
class Flux:
    account: str
    token: str = field(repr=False)
    net: web.Net
    api: str = API

    def draw(self, prompt: str) -> bytes:
        """One picture for `prompt` (cut to the model's limit)."""
        body = json.dumps({"prompt": prompt[:MAX_PROMPT], "steps": STEPS}).encode()
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        url = f"{self.api}/accounts/{self.account}/ai/run/{MODEL}"
        return parse(self.net.send(web.Request("POST", url, "workers ai", body, headers)).body)


def from_env(env: Mapping[str, str], net: web.Net) -> Flux | None:
    """The drafter when CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are set; None
    when neither is (Tara makes every picture herself, as before)."""
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip().lower()
    token = env.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account and not token:
        return None
    if not account or not token:
        raise config.ConfigError(["set both CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN"])
    if not _ACCOUNT.match(account):
        raise config.ConfigError(["CLOUDFLARE_ACCOUNT_ID must be 32 hex characters"])
    return Flux(account, token, net)
