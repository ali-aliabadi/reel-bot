"""`reel-bot morning`: one step of the morning pick through Relay (see morning_write)."""

import argparse
import random
import sqlite3
import sys
from collections.abc import Callable, Mapping
from datetime import datetime

from reel_bot import config, morning, morning_write, relay_api, store, web

EXIT_CONFIG = 2
EXIT_REFUSED = 3


def add_parser(add: Callable[..., argparse.ArgumentParser]) -> None:
    mo = add("morning", help="ask ali to pick today's story through Relay")
    mo.add_argument("--check", action="store_true", help="only record an answer; never ask")


def run(
    env: Mapping[str, str],
    clock: Callable[[], datetime],
    open_db: Callable[[Mapping[str, str]], sqlite3.Connection],
    *,
    ask: bool = True,
) -> int:
    try:
        cfg = config.load(env)
    except config.ConfigError as err:
        print(f"config: {err.problems[0]}", file=sys.stderr)
        return EXIT_CONFIG
    relay = relay_api.Client(
        cfg.relay_url, cfg.relay_api_key, cfg.relay_app, web.Web(relay_api.TIMEOUT_S)
    )
    ctx = morning_write.Context(relay, cfg.relay_user, clock, random.SystemRandom())
    conn = open_db(env)
    try:
        print(morning_write.run(conn, ctx, ask=ask))
    except (morning.MorningError, relay_api.RelayError, store.StoreError) as err:
        print(f"morning: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    return 0
