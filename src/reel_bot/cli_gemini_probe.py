"""One-image API experiment, never part of the daily pipeline.

Run only with a key belonging to a project whose billing is disabled. The
Gemini API has no per-request free-only switch: the project must enforce it.
Google currently lists image models as paid-only, so quota refusal is expected.
"""

import argparse
import base64
import binascii
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from reel_bot import visuals, web

MODELS = ("gemini-3.1-flash-image", "gemini-3.1-flash-lite-image")
API = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT_S = 90.0
MAX_BYTES = 5 << 20
MAX_PROMPT = 4000


class ProbeError(Exception):
    pass


def parse(body: bytes) -> bytes:
    """Extract a single bounded PNG/JPEG/WebP; never expose response text."""
    try:
        data = json.loads(body)
        parts = data["candidates"][0]["content"]["parts"]
        for part in parts:
            if "inlineData" not in part:
                continue
            encoded = part["inlineData"]["data"]
            if not isinstance(encoded, str) or len(encoded) > MAX_BYTES * 4 // 3 + 4:
                raise ProbeError("Gemini image is invalid or over 5 MB")
            raw = base64.b64decode(encoded, validate=True)
            if not raw or len(raw) > MAX_BYTES:
                raise ProbeError("Gemini image is empty or over 5 MB")
            visuals.suffix(raw)
            return raw
    except (
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        RecursionError,
        binascii.Error,
        visuals.VisualsError,
    ) as err:
        raise ProbeError("Gemini did not return a valid image") from err
    raise ProbeError("Gemini returned no image (possibly blocked or text-only)")


def generate(key: str, model: str, prompt: str, net: web.Net) -> bytes:
    """Exactly one request, no retries or automatic provider fallback."""
    if model not in MODELS or not key or not prompt.strip() or len(prompt) > MAX_PROMPT:
        raise ProbeError("provide a supported image model, API key and 1-4000 character prompt")
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
    ).encode()
    request = web.Request(
        "POST",
        f"{API}/{model}:generateContent",
        "gemini image probe",
        body,
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        retry=False,
    )
    return parse(net.send(request).body)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path, required=True, help="output stem; image suffix is detected"
    )
    parser.add_argument(
        "--billing-disabled",
        action="store_true",
        required=True,
        help="confirm the API key's project has billing disabled",
    )
    args = parser.parse_args(argv)
    if any(args.out.with_suffix(s).exists() for s in visuals.SUFFIXES):
        print("probe: output already exists; refusing another generation", file=sys.stderr)
        return 1
    try:
        raw = generate(
            os.environ.get("GEMINI_API_KEY", "").strip(),
            args.model,
            args.prompt_file.read_text("utf-8"),
            web.Web(TIMEOUT_S),
        )
        out = args.out.with_suffix(visuals.suffix(raw))
        out.write_bytes(raw)
    except (ProbeError, web.WebError, OSError, UnicodeError) as err:
        print(f"probe: {err}", file=sys.stderr)
        return 1
    print(f"Saved {out}. Verify the project's quota and billing before any further use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
