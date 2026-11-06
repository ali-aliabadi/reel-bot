"""Draft pictures: reel-bot draws each picture first (`flux`), and Tara chooses.

Each draft goes to Tara with its prompt and two buttons: use the draft, or make
her own in ChatGPT (then she gets the usual picture question). A draft that
can't be made is logged and skipped, and Tara gets the usual question. Drafts
are saved as `draft-<n>.<ext>` in the video's folder and reused on a rerun, so
no picture is drawn twice. Only the picture Tara picks is the video's picture.
"""

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import cast

from reel_bot import flux, visuals, web

log = logging.getLogger(__name__)

Draw = Callable[[str], bytes]  # a prompt to a picture's bytes
USE = "Use this picture"
OWN = "I'll make my own"
_TYPES = {".png": "image/png", ".jpg": "image/jpeg"}  # what Relay's image block shows


def found(folder: Path, number: int) -> Path | None:
    """Picture `number`'s draft, whichever type it is, or None."""
    paths = (folder / f"draft-{number}{s}" for s in visuals.SUFFIXES)
    return next((p for p in paths if p.is_file()), None)


def make(folder: Path, number: int, prompt: str, draw: Draw | None) -> Path | None:
    """Picture `number`'s draft, drawn now unless one is on disk; None when there
    is no drafter or the draw failed (Tara then makes the picture herself)."""
    if (old := found(folder, number)) is not None or draw is None:
        return old
    try:
        data = draw(prompt)
        suffix = visuals.suffix(data)
    except (flux.FluxError, web.WebError, visuals.VisualsError) as err:
        log.warning("no draft picture", extra={"picture": number, "error": str(err)})
        return None
    if suffix not in _TYPES:
        log.warning("no draft picture", extra={"picture": number, "error": f"a {suffix} draft"})
        return None
    path = folder / f"draft-{number}{suffix}"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    log.info("draft picture made", extra={"picture": number})
    return path


def message(plain: dict[str, object], draft: Path) -> dict[str, object]:
    """The usual picture question (`relay_api.picture_message`) with the draft shown
    first and two buttons in place of the picture reply: use it, or make her own."""
    prompt = cast(list[object], plain["blocks"])[0]
    return {
        **plain,
        "blocks": [
            {
                "type": "image",
                "content_type": _TYPES[draft.suffix],
                "base64": base64.b64encode(draft.read_bytes()).decode("ascii"),
                "caption": "reel-bot's draft of this picture",
            },
            prompt,
            {
                "type": "question",
                "text": "Use this picture, or make your own in ChatGPT from the prompt?",
                "options": [USE, OWN],
            },
        ],
    }
