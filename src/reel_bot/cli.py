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
    captions_write,
    config,
    logs,
    pipeline,
    render_write,
    script_write,
    store,
    store_videos,
    stories,
    videos,
    visuals_write,
    voice,
)

EXIT_CONFIG = 2
EXIT_REFUSED = 3
Clock = Callable[[], datetime]


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
    re = sub.add_parser("render", help="render the 1080x1920 MP4 with ffmpeg (cached)")
    re.add_argument("video_id")
    mk = sub.add_parser("make", help="run every stage the video is ready for (cached)")
    mk.add_argument("video_id")
    mk.add_argument("--voice", type=Path, help="Tara's new take; redoes captions and render")
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


Stage = Callable[[sqlite3.Connection, Path], str]


def _stage(env: Mapping[str, str], name: str, action: Stage) -> int:
    """Run one stage command: print its result line, or its error and exit 3."""
    out_dir = Path(env.get("REEL_BOT_OUT_DIR") or config.DEFAULT_OUT_DIR)
    conn = _open(env)
    try:
        line = action(conn, out_dir)
    except (*pipeline.ERRORS, store.StoreError, videos.VideoError, OSError) as err:
        print(f"{name}: {err}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        conn.close()
    print(line)
    return 0


def _stages(args: argparse.Namespace, env: Mapping[str, str], clock: Clock) -> dict[str, Stage]:
    vid = getattr(args, "video_id", "")
    key = env.get("OPENAI_API_KEY", "").strip()

    def script_(conn: sqlite3.Connection, out: Path) -> str:
        s = script_write.write(conn, out, vid, script_write.claude(), clock)
        return f"{out / vid / script_write.FILENAME}\t{s.words()} words"

    def voice_(conn: sqlite3.Connection, out: Path) -> str:
        v = voice.import_take(conn, out, vid, args.recording, clock)
        return f"{v.path}\t{v.seconds:.1f} s"

    def captions_(conn: sqlite3.Connection, out: Path) -> str:
        c = captions_write.make(conn, out, vid, captions_write.openai(key), clock)
        path = out / vid / captions_write.FILENAME
        return f"{path}\t{len(c.cues)} captions\t{c.matched:.0%} of the script heard"

    def visuals_(conn: sqlite3.Connection, out: Path) -> str:
        v = visuals_write.make(conn, out, vid, visuals_write.openai(key), clock)
        return f"{out / vid / visuals_write.FILENAME}\t{len(v.pictures)} pictures"

    def render_(conn: sqlite3.Connection, out: Path) -> str:
        r = render_write.make(conn, out, vid, clock)
        return f"{r.path}\t{r.seconds:.1f} s"

    def make_(conn: sqlite3.Connection, out: Path) -> str:
        ctx = pipeline.Context(
            out_dir=out,
            clock=clock,
            claude=script_write.claude,
            transcribe=lambda: captions_write.openai(key),
            draw=lambda: visuals_write.openai(key),
        )
        p = pipeline.advance(conn, vid, args.voice, ctx)
        lines = [f"{r.stage}\t${r.cost_usd:.3f}\t{r.seconds:.0f}s" for r in p.runs]
        lines.append(f"total\t${p.cost_usd():.3f}\t{p.seconds():.0f}s")
        lines.append(f"done: {p.render}" if p.render else f"waiting for {p.waiting}")
        return "\n".join(lines)

    return {
        "script": script_,
        "voice": voice_,
        "captions": captions_,
        "visuals": visuals_,
        "render": render_,
        "make": make_,
    }


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
    }
    if args.command in handlers:
        return handlers[args.command]()
    stages = _stages(args, env, clock)
    if args.command in stages:
        return _stage(env, args.command, stages[args.command])
    print(__version__)
    return 0
