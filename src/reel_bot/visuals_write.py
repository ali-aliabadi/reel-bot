"""Visuals stage, I/O edge: draw each paragraph's picture with OpenAI's image API.

Pictures can be drawn as soon as the script exists, while Tara is still
reading. Each picture is saved as soon as it arrives, so a rerun after a
failure draws only the missing ones; a finished stage is reused whole. Every
picture is recorded as an asset with its source and licence.
"""

import base64
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from reel_bot import openai_api, script, store_videos, videos, visuals

log = logging.getLogger(__name__)

MODEL = "gpt-image-1"
SIZE = "1024x1536"  # portrait; the render stage scales and pans it to 1080x1920
QUALITY = "medium"
PRICE_PER_IMAGE = 0.063  # US dollars for one medium 1024x1536 picture
TIMEOUT_S = 180.0
SOURCE = f"openai:{MODEL}"
LICENCE = "OpenAI terms of use: output assigned to the user, commercial use allowed"
FILENAME = "visuals.json"


@dataclass(frozen=True)
class Drawn:
    png: bytes
    cost_usd: float


# picture prompt -> the picture
Draw = Callable[[str], Drawn]


def parse(raw: bytes) -> bytes:
    try:
        data = cast(dict[str, Any], json.loads(raw))
        return base64.b64decode(cast(list[dict[str, str]], data["data"])[0]["b64_json"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError) as err:
        raise visuals.VisualsError("the image reply has no picture") from err


def openai(
    api_key: str,
    base_url: str = openai_api.API_URL,
    sleep: Callable[[float], None] = time.sleep,
) -> Draw:
    """A Draw backed by OpenAI's image API."""
    try:
        client = openai_api.Client(api_key, base_url, TIMEOUT_S, sleep)
    except openai_api.OpenAIError as err:
        raise visuals.VisualsError(str(err)) from err

    def draw(prompt: str) -> Drawn:
        body = json.dumps(
            {"model": MODEL, "prompt": prompt, "size": SIZE, "quality": QUALITY, "n": 1}
        ).encode()
        try:
            raw = client.post("/images/generations", body, "application/json", "picture")
        except openai_api.OpenAIError as err:
            raise visuals.VisualsError(str(err)) from err
        return Drawn(parse(raw), PRICE_PER_IMAGE)

    return draw


def _manifest(v: visuals.Visuals) -> str:
    pictures = [{"section": p.section, "path": p.path, "prompt": p.prompt} for p in v.pictures]
    return json.dumps({"video_id": v.video_id, "pictures": pictures}, indent=2)


def make(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    draw: Draw,
    clock: Callable[[], datetime],
) -> visuals.Visuals:
    """The video's pictures: reused when already drawn, otherwise drawn and recorded."""
    store_videos.get_video(conn, video_id)  # an unknown video is refused here
    folder = videos.video_dir(out_dir, video_id)
    runs = store_videos.stage_runs(conn, video_id)
    if "script" not in runs:
        raise visuals.VisualsError(f"video {video_id} has no script yet")
    plan = visuals.plan(script.from_json((folder / "script.json").read_text(encoding="utf-8")))
    if "visuals" in runs and all((folder / p.path).is_file() for p in plan.pictures):
        log.info("visuals reused", extra={"video_id": video_id})
        return plan

    started, cost = clock(), 0.0
    for pic in plan.pictures:
        path = folder / pic.path
        if path.is_file():
            continue  # drawn by an earlier run that stopped part way
        drawn = draw(pic.prompt)
        visuals.check_png(drawn.png)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(drawn.png)
        tmp.replace(path)
        cost += drawn.cost_usd
        store_videos.add_asset(
            conn, video_id, videos.Asset(pic.path, "image", SOURCE, LICENCE, clock())
        )
        log.info("picture drawn", extra={"video_id": video_id, "section": pic.section})
    (folder / FILENAME).write_text(_manifest(plan), encoding="utf-8")
    finished = clock()
    store_videos.record_stage(
        conn,
        video_id,
        videos.StageRun(
            "visuals", FILENAME, round(cost, 4), (finished - started).total_seconds(), finished
        ),
    )
    log.info("visuals made", extra={"video_id": video_id, "cost_usd": round(cost, 4)})
    return plan
