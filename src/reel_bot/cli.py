"""Command-line entry point. Subcommands are added here as roadmap items land."""

import argparse
import os
import random
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from reel_bot import __version__, config, logs, store, store_videos, stories, videos

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
    vid = sub.add_parser("video", help="create a video and record ali's story pick")
    vid_sub = vid.add_subparsers(dest="action", required=True)
    new = vid_sub.add_parser("new", help="create a video and print its ID")
    new.add_argument("--framing", choices=videos.FRAMINGS, required=True)
    new.add_argument("--length", choices=videos.LENGTHS, required=True)
    show = vid_sub.add_parser("show", help="show a video's settings, pick and stages")
    show.add_argument("video_id")
    choose = vid_sub.add_parser("choose", help="record the human pick (spends the tale)")
    choose.add_argument("video_id")
    choose.add_argument("story_id")
    choose.add_argument("--by", required=True, help="who chose, e.g. ali")
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


def _open(env: Mapping[str, str]) -> sqlite3.Connection:
    conn = store.open_db(Path(env.get("REEL_BOT_DB_PATH") or config.DEFAULT_DB_PATH))
    store.sync_stories(conn, stories.load_builtin())
    return conn


def _video_action(conn: sqlite3.Connection, args: argparse.Namespace, now: datetime) -> None:
    if args.action == "new":
        v = videos.Video(videos.new_id(now), now, args.framing, args.length)
        store_videos.add_video(conn, v)
        print(v.id)
        return
    if args.action == "choose":
        store_videos.choose_story(conn, args.video_id, args.story_id, args.by, now)
        return
    v = store_videos.get_video(conn, args.video_id)
    print(f"{v.id}\tframing={v.framing}\tlength={v.length}\tstory={v.story_id or '-'}")
    if v.chosen_by and v.chosen_at:
        print(f"chosen by {v.chosen_by} at {v.chosen_at.isoformat()}")
    for run in store_videos.stage_runs(conn, v.id).values():
        print(f"{run.stage}\t{run.path}\t${run.cost_usd:.2f}\t{run.seconds:.0f}s")


def _video(args: argparse.Namespace, env: Mapping[str, str], now: datetime) -> int:
    conn = _open(env)
    try:
        _video_action(conn, args, now)
    except store.StoreError as err:
        print(f"video: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    return 0


def _stories(args: argparse.Namespace, env: Mapping[str, str], now: datetime) -> int:
    conn = _open(env)
    try:
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
    if args.command == "video":
        return _video(args, env, clock())
    print(__version__)
    return 0
