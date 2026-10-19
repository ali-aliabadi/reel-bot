"""Command-line entry point. Subcommands are added here as roadmap items land."""

import argparse
import os
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from reel_bot import __version__, config, logs, store, stories

EXIT_CONFIG = 2
EXIT_REFUSED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reel-bot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version")
    sub.add_parser("config", help="check the configuration and show it without secrets")
    st = sub.add_parser("stories", help="the story catalog (each tale is told only once)")
    st_sub = st.add_subparsers(dest="action")
    st_sub.add_parser("list", help="list the stories whose tale has not been told (default)")
    pick = st_sub.add_parser("pick", help="suggest unused stories from different tales")
    pick.add_argument("-n", type=int, default=3, help="how many (default 3)")
    use = st_sub.add_parser("use", help="record that a video tells a story")
    use.add_argument("story_id")
    use.add_argument("video_id")
    return parser


def _show_config(env: Mapping[str, str]) -> int:
    try:
        cfg = config.load(env)
    except config.ConfigError as err:
        for problem in err.problems:
            print(f"config: {problem}", file=sys.stderr)
        return EXIT_CONFIG
    for name, value in cfg.describe().items():
        print(f"{name}={value}")
    return 0


def _print_story(story: stories.Story) -> None:
    print(f"{story.id}\t{story.title} ({story.collection})")


def _stories(args: argparse.Namespace, env: Mapping[str, str], now: datetime) -> int:
    path = Path(env.get("REEL_BOT_DB_PATH") or config.DEFAULT_DB_PATH)
    conn = store.open_db(path)
    try:
        store.sync_stories(conn, stories.load_builtin())
        if args.action == "use":
            try:
                store.mark_used(conn, args.story_id, args.video_id, now)
            except store.StoreError as err:
                print(f"stories: {err}", file=sys.stderr)
                return EXIT_REFUSED
            return 0
        used = store.used_tales(conn)
        catalog = store.all_stories(conn)
        if args.action == "pick":
            for story in stories.candidates(catalog, used, args.n, random.SystemRandom()):
                _print_story(story)
            return 0
        for story in catalog:
            if story.tale not in used:
                _print_story(story)
        return 0
    finally:
        conn.close()


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env
    try:
        logs.setup(env.get("REEL_BOT_LOG_LEVEL", "").strip().lower() or "info")
    except ValueError as err:
        print(f"config: REEL_BOT_LOG_LEVEL: {err}", file=sys.stderr)
        return EXIT_CONFIG
    if args.command == "config":
        return _show_config(env)
    if args.command == "stories":
        return _stories(args, env, clock())
    print(__version__)
    return 0
