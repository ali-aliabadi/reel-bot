"""Script stage, I/O edge: ask Claude for the script, check it, cache it.

`write` is idempotent: when the video already has a recorded script on disk it
is returned without another model call. `add` records a script written
elsewhere (ChatGPT, pasted back by Tara) once it passes the same checks. A video
can only get a script after its story was picked by a person (the human pick rule).
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


def _picked(conn: sqlite3.Connection, video_id: str) -> tuple[videos.Video, Story]:
    video = store_videos.get_video(conn, video_id)
    if video.story_id is None:
        raise script.ScriptError(f"video {video_id} has no picked story yet")
    return video, _story(conn, video.story_id)


def _save(conn: sqlite3.Connection, path: Path, draft: script.Script, run: videos.StageRun) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(script.to_json(draft), encoding="utf-8")
    tmp.replace(path)
    store_videos.record_stage(conn, draft.video_id, run)


def add(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    reply: str,
    clock: Callable[[], datetime],
) -> list[str]:
    """Check a script written elsewhere and record it (at no cost) when it is usable.
    Returns its problems; empty once it is recorded."""
    video, story = _picked(conn, video_id)
    try:
        draft = script.parse(reply, story, video)
    except script.ScriptError:
        return ["it is not the JSON asked for"]
    found = script.problems(draft, video)
    if not found:
        path = videos.video_dir(out_dir, video_id) / FILENAME
        _save(conn, path, draft, videos.StageRun("script", FILENAME, 0.0, 0.0, clock()))
    log.info("script pasted", extra={"video_id": video_id, "problems": len(found)})
    return found


def write(
    conn: sqlite3.Connection,
    out_dir: Path,
    video_id: str,
    generate: Generate,
    clock: Callable[[], datetime],
) -> script.Script:
    """The video's script: from the cache, or written by the model and checked."""
    video, story = _picked(conn, video_id)
    path = videos.video_dir(out_dir, video_id) / FILENAME
    if "script" in store_videos.stage_runs(conn, video_id) and path.is_file():
        log.info("script reused", extra={"video_id": video_id})
        return script.from_json(path.read_text(encoding="utf-8"))

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

    finished = clock()
    seconds = (finished - started).total_seconds()
    _save(conn, path, draft, videos.StageRun("script", FILENAME, round(cost, 4), seconds, finished))
    return draft
