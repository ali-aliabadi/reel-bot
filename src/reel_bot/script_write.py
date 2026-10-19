"""Script stage, I/O edge: ask Claude for the script, check it, cache it.

`write` is idempotent: when the video already has a recorded script on disk it
is returned without another model call. A video can only get a script after
its story was picked by a person (the human pick rule).
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

from reel_bot import script, store, store_videos, videos
from reel_bot.stories import Story

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ATTEMPTS = 2  # one retry with the checker's feedback, then give up
TIMEOUT_S = 180.0
FILENAME = "script.json"
# US dollars per million input and output tokens. A refusal fallback may answer
# from another model; unknown models are priced at the dearest listed rate.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-4-8": (5.0, 25.0)}


@dataclass(frozen=True)
class Reply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int

    def cost_usd(self) -> float:
        rate_in, rate_out = PRICES.get(self.model, max(PRICES.values()))
        return (self.input_tokens * rate_in + self.output_tokens * rate_out) / 1_000_000


# (system prompt, user message, JSON schema) -> the model's reply
Generate = Callable[[str, str, dict[str, Any]], Reply]


def claude(client: anthropic.Anthropic | None = None) -> Generate:
    """A Generate backed by the Claude API. The key comes from the environment
    (ANTHROPIC_API_KEY) and is never logged."""
    api = client or anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=2)

    def generate(system: str, user: str, schema: dict[str, Any]) -> Reply:
        try:
            resp = api.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as err:
            raise script.ScriptError(f"Claude API call failed: {type(err).__name__}") from err
        if resp.stop_reason != "end_turn":
            raise script.ScriptError(f"Claude stopped with {resp.stop_reason}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Reply(text, resp.model, resp.usage.input_tokens, resp.usage.output_tokens)

    return generate


def _story(conn: sqlite3.Connection, story_id: str) -> Story:
    for s in store.all_stories(conn):
        if s.id == story_id:
            return s
    raise script.ScriptError(f"story {story_id!r} is not in the catalog")


def write(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    generate: Generate,
    clock: Callable[[], datetime],
) -> script.Script:
    """The video's script: from the cache, or written by the model and checked."""
    video = store_videos.get_video(conn, video_id)
    if video.story_id is None:
        raise script.ScriptError(f"video {video_id} has no picked story yet")
    path = videos.video_dir(out_dir, video_id) / FILENAME
    if "script" in store_videos.stage_runs(conn, video_id) and path.is_file():
        log.info("script reused", extra={"video_id": video_id})
        return script.from_json(path.read_text(encoding="utf-8"))

    story = _story(conn, video.story_id)
    started, cost = clock(), 0.0
    user = script.prompt(story, video)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = generate(script.SYSTEM, user, script.SCHEMA)
        cost += reply.cost_usd()
        draft = script.parse(reply.text, story, video)
        found = script.problems(draft, video)
        log.info(
            "script draft",
            extra={
                "video_id": video_id,
                "attempt": attempt,
                "words": draft.words(),
                "problems": len(found),
                "cost_usd": round(cost, 4),
            },
        )
        if not found:
            break
        user = (
            script.prompt(story, video)
            + "\n\nA first draft had these problems; write a new script without them:\n- "
            + "\n- ".join(found)
        )
    else:
        raise script.ScriptError(f"no usable script for {video_id} after {MAX_ATTEMPTS} tries")

    tmp = path.with_suffix(".tmp")
    tmp.write_text(script.to_json(draft), encoding="utf-8")
    tmp.replace(path)
    finished = clock()
    seconds = (finished - started).total_seconds()
    store_videos.record_stage(
        conn, video_id, videos.StageRun("script", FILENAME, round(cost, 4), seconds, finished)
    )
    return draft
