"""`reel-bot daily`: the Relay-driven daily run (see `daily`)."""

import argparse
import random
import sqlite3
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from reel_bot import (
    budget,
    captions_write,
    config,
    daily,
    daily_run,
    guide,
    pipeline,
    relay_api,
    script_write,
    store,
    videos,
    web,
)

EXIT_CONFIG = 2
EXIT_REFUSED = 3
RELAY_TIMEOUT_S = 180.0  # room to upload a 45 MB video


def add_parser(add: Callable[..., argparse.ArgumentParser]) -> None:
    da = add("daily", help="the daily run: pick, Tara's take, video to Tara (Relay)")
    da_sub = da.add_subparsers(dest="action", required=True)
    st = da_sub.add_parser("start", help="make today's video and ask ali to pick its story")
    st.add_argument("--framing", choices=videos.FRAMINGS, required=True)
    st.add_argument("--length", choices=videos.LENGTHS, required=True)
    da_sub.add_parser("tick", help="move every open video on (run every few minutes)")
    re = da_sub.add_parser("retry", help="reopen a failed video")
    re.add_argument("video_id")


def _context(
    conn: sqlite3.Connection, env: Mapping[str, str], clock: Callable[[], datetime], cap: float
) -> Callable[[str], pipeline.Context]:
    out = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)

    def make(vid: str) -> pipeline.Context:
        b = budget.Budget(conn, vid, cap, clock)
        return pipeline.Context(
            out_dir=out,
            clock=clock,
            claude=lambda: b.claude(script_write.claude()),
            transcribe=captions_write.local,
            speak=guide.local,
        )

    return make


def _run(
    conn: sqlite3.Connection, env: Mapping[str, str], clock: Callable[[], datetime], cap: float
) -> daily_run.Run:
    cfg = config.load(env)
    narrator = env.get("REEL_BOT_NARRATOR", "").strip()
    if not narrator:
        raise config.ConfigError(["REEL_BOT_NARRATOR (Tara's Relay username) is required"])
    relay = relay_api.Relay(
        cfg.relay_url, cfg.relay_api_key, cfg.relay_app, web.Web(RELAY_TIMEOUT_S)
    )
    people = daily_run.People(cfg.relay_user, narrator)
    return daily_run.Run(conn, relay, people, clock, _context(conn, env, clock, cap))


def main(
    args: argparse.Namespace,
    conn: sqlite3.Connection,
    env: Mapping[str, str],
    clock: Callable[[], datetime],
    cap: float,
) -> int:
    if args.action == "retry":
        try:
            daily.retry(conn, args.video_id)
        except daily_run.DailyError as err:
            print(f"daily: {err}", file=sys.stderr)
            return EXIT_REFUSED
        print(f"{args.video_id}\treopened; the next tick carries on")
        return 0
    try:
        run = _run(conn, env, clock, cap)
    except config.ConfigError as err:
        for problem in err.problems:
            print(f"config: {problem}", file=sys.stderr)
        return EXIT_CONFIG
    if args.action == "tick":
        for line in daily.tick(run):
            print(line)
        return 0
    try:
        vid = daily.start(run, args.framing, args.length, random.SystemRandom())
    except (daily_run.DailyError, relay_api.RelayError, web.WebError, store.StoreError) as err:
        print(f"daily: {err}", file=sys.stderr)
        return EXIT_REFUSED
    print(vid or "an earlier pick is still unanswered; nothing sent")
    return 0
