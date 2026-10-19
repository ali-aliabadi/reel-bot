"""Command-line entry point. Subcommands are added here as roadmap items land."""

import argparse
import os
import random
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from reel_bot import (
    __version__,
    captions,
    captions_write,
    config,
    logs,
    script,
    script_write,
    store,
    store_videos,
    stories,
    videos,
    visuals,
    visuals_write,
    voice,
)

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
    sc = sub.add_parser("script", help="write a picked video's script with Claude (cached)")
    sc.add_argument("video_id")
    vo = sub.add_parser("voice", help="import Tara's recording: clean it and set its loudness")
    vo.add_argument("video_id")
    vo.add_argument("recording", type=Path, help="the audio file (voice note, m4a, wav)")
    ca = sub.add_parser("captions", help="time the script's words on Tara's take (cached)")
    ca.add_argument("video_id")
    vi = sub.add_parser("visuals", help="draw one picture per paragraph with OpenAI (cached)")
    vi.add_argument("video_id")
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


def _script(args: argparse.Namespace, env: Mapping[str, str], clock: Callable[[], datetime]) -> int:
    out_dir = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)
    conn = _open(env)
    try:
        s = script_write.write(conn, out_dir, args.video_id, script_write.claude(), clock)
    except (script.ScriptError, store.StoreError, videos.VideoError) as err:
        print(f"script: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    print(f"{out_dir / args.video_id / script_write.FILENAME}\t{s.words()} words")
    return 0


def _voice(args: argparse.Namespace, env: Mapping[str, str], clock: Callable[[], datetime]) -> int:
    out_dir = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)
    conn = _open(env)
    try:
        v = voice.import_take(conn, out_dir, args.video_id, args.recording, clock)
    except (voice.VoiceError, store.StoreError, videos.VideoError) as err:
        print(f"voice: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    print(f"{v.path}\t{v.seconds:.1f} s")
    return 0


def _captions(
    args: argparse.Namespace, env: Mapping[str, str], clock: Callable[[], datetime]
) -> int:
    out_dir = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)
    conn = _open(env)
    try:
        transcribe = captions_write.openai(env.get("OPENAI_API_KEY", "").strip())
        c = captions_write.make(conn, out_dir, args.video_id, transcribe, clock)
    except (captions.CaptionsError, store.StoreError, videos.VideoError, OSError) as err:
        print(f"captions: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    path = out_dir / args.video_id / captions_write.FILENAME
    print(f"{path}\t{len(c.cues)} captions\t{c.matched:.0%} of the script heard")
    return 0


def _visuals(
    args: argparse.Namespace, env: Mapping[str, str], clock: Callable[[], datetime]
) -> int:
    out_dir = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)
    conn = _open(env)
    try:
        draw = visuals_write.openai(env.get("OPENAI_API_KEY", "").strip())
        v = visuals_write.make(conn, out_dir, args.video_id, draw, clock)
    except (visuals.VisualsError, store.StoreError, videos.VideoError, OSError) as err:
        print(f"visuals: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    print(f"{out_dir / args.video_id / visuals_write.FILENAME}\t{len(v.pictures)} pictures")
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
    handlers: dict[str, Callable[[], int]] = {
        "config": lambda: _show_config(env),
        "stories": lambda: _stories(args, env, clock()),
        "video": lambda: _video(args, env, clock()),
        "script": lambda: _script(args, env, clock),
        "voice": lambda: _voice(args, env, clock),
        "captions": lambda: _captions(args, env, clock),
        "visuals": lambda: _visuals(args, env, clock),
    }
    if args.command in handlers:
        return handlers[args.command]()
    print(__version__)
    return 0
