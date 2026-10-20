"""The posting commands' command-line side: `reel-bot auth` and the publish parsers."""

import argparse
import getpass
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from reel_bot import config, publish, publish_write, web

EXIT_REFUSED = 3
ERRORS = (publish.PublishError, web.WebError)


def add_parsers(add: Callable[..., argparse.ArgumentParser]) -> None:
    pu = add("publish", help="post a picked, rendered video (after reel-bot auth)")
    pu.add_argument("video_id")
    pu.add_argument("--to", nargs="+", choices=publish.PLATFORMS, help="default: all signed in")
    au = add("auth", help="sign in to a platform once; saved next to the database")
    au.add_argument("platform", choices=publish.PLATFORMS)


def tokens(env: Mapping[str, str]) -> Path:
    """The sign-ins file, next to the database."""
    db = Path(env.get("REEL_BOT_DB_PATH") or config.DEFAULT_DB_PATH)
    return db.with_name(publish_write.TOKENS)


def auth(platform: str, env: Mapping[str, str], clock: Callable[[], datetime]) -> int:
    out_dir = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)
    ctx = publish_write.Context(out_dir, tokens(env), clock, web.Web())
    try:
        publish_write.sign_in(
            platform, env, ctx, lambda q, hide: (getpass.getpass if hide else input)(q)
        )
    except ERRORS as err:
        print(f"auth: {err}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"signed in to {platform}; saved in {ctx.tokens}")
    return 0
