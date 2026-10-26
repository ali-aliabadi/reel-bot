"""Publish stage, I/O edge: platform sign-ins, uploads and the `posts` table.

Sign-ins live in one owner-only JSON file next to the database (`tokens.json`),
never in git, never logged or printed. A platform that already has the video is
skipped, so a rerun after a failure posts only where it failed.
"""

import json
import logging
import os
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

from reel_bot import (
    instagram,
    publish,
    script,
    script_write,
    store,
    store_videos,
    tiktok,
    videos,
    youtube,
)
from reel_bot.publish import PublishError
from reel_bot.web import Net, WebError

log = logging.getLogger(__name__)

TOKENS = "tokens.json"
Ask = Callable[[str, bool], str]


@dataclass(frozen=True)
class Published:
    posts: dict[str, str]  # platform: the platform's ID, including earlier runs
    failures: list[str] = field(default_factory=list[str])


def load_tokens(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError) as err:
        raise PublishError(f"{path} is damaged; run reel-bot auth again") from err
    if not isinstance(data, dict):
        raise PublishError(f"{path} is damaged; run reel-bot auth again")
    return {
        str(k): {str(a): str(b) for a, b in cast(dict[str, object], v).items()}
        for k, v in cast(dict[str, object], data).items()
        if isinstance(v, dict)
    }


def save_tokens(path: Path, platform: str, values: Mapping[str, str]) -> None:
    """Replace one platform's sign-in; the file is written owner-only, then swapped in."""
    data = load_tokens(path) | {platform: dict(values)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)  # O_CREAT keeps an old file's looser mode
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    tmp.replace(path)


@dataclass(frozen=True)
class Context:
    out_dir: Path
    tokens: Path  # the sign-ins file
    clock: Callable[[], datetime]
    net: Net


def apps(env: Mapping[str, str]) -> tuple[youtube.App, tiktok.App]:
    def get(name: str, default: str = "") -> str:
        return env.get(name, "").strip() or default

    return (
        youtube.App(
            get("YOUTUBE_CLIENT_ID"), get("YOUTUBE_CLIENT_SECRET"), get("YOUTUBE_PRIVACY", "public")
        ),
        tiktok.App(
            get("TIKTOK_CLIENT_KEY"),
            get("TIKTOK_CLIENT_SECRET"),
            get("TIKTOK_REDIRECT_URI"),
            get("TIKTOK_PRIVACY", "SELF_ONLY"),
        ),
    )


def uploaders(env: Mapping[str, str], ctx: Context) -> dict[str, publish.Uploader]:
    yt, tt = apps(env)
    net = ctx.net
    return {
        "youtube": publish.Uploader(
            lambda saved: youtube.access(net, yt, saved),
            lambda token, _saved, mp4, post: youtube.upload(net, yt, token, mp4, post),
        ),
        "instagram": publish.Uploader(
            lambda saved: instagram.access(net, saved, ctx.clock()),
            lambda token, saved, mp4, post: instagram.upload(net, token, saved, mp4, post),
        ),
        "tiktok": publish.Uploader(
            lambda saved: tiktok.access(net, tt, saved),
            lambda token, _saved, mp4, post: tiktok.upload(net, tt, token, mp4, post),
        ),
    }


def sign_in(platform: str, env: Mapping[str, str], ctx: Context, ask: Ask) -> None:
    """`reel-bot auth <platform>`: one interactive sign-in, saved to the tokens file.
    `ask(prompt, hidden)` reads a line; hidden for a pasted token."""
    if platform == "instagram":
        token = ask("Paste the Instagram access token from the Meta app dashboard: ", True)
        save_tokens(ctx.tokens, platform, instagram.sign_in(ctx.net, token.strip(), ctx.clock()))
        return
    yt, tt = apps(env)
    state = secrets.token_urlsafe(16)
    url = youtube.sign_in_url(yt, state) if platform == "youtube" else tiktok.sign_in_url(tt, state)
    pasted = ask(
        "Open this in a browser, approve, then paste the address the browser lands on"
        f" (it may say the page can't be reached; that is fine):\n{url}\n> ",
        False,
    )
    code = publish.code_from(pasted, state)
    if platform == "youtube":
        got = youtube.exchange(ctx.net, yt, code)
    else:
        got = tiktok.exchange(ctx.net, tt, code)
    save_tokens(ctx.tokens, platform, got)


def make(
    conn: sqlite3.Connection, video_id: str, to: Mapping[str, publish.Uploader], ctx: Context
) -> Published:
    """Post a picked, rendered video to each platform in `to` that doesn't have it yet."""
    video = store_videos.get_video(conn, video_id)
    runs = store_videos.stage_runs(conn, video_id)
    publish.check_ready(video, runs)
    folder = videos.video_dir(ctx.out_dir, video_id)
    raw = (folder / script_write.FILENAME).read_text(encoding="utf-8")
    picked = [s for s in store.all_stories(conn) if s.id == video.story_id]
    post = publish.post_for(script.from_json(raw), picked[0].collection if picked else "")
    mp4 = folder / runs["render"].path
    done = store_videos.posts(conn, video_id)
    failures: list[str] = []
    for platform, up in to.items():
        if platform in done:
            continue
        saved = load_tokens(ctx.tokens).get(platform)
        if saved is None:
            failures.append(f"{platform}: not signed in (run reel-bot auth {platform})")
            continue
        try:
            token, fresh = up.access(saved)
            save_tokens(ctx.tokens, platform, fresh)
            pid = up.upload(token, fresh, mp4, post)
        except (PublishError, WebError, OSError) as err:
            log.warning("post failed", extra={"video_id": video_id, "platform": platform})
            failures.append(f"{platform}: {err}")
            continue
        store_videos.add_post(conn, video_id, platform, pid, ctx.clock())
        log.info("posted", extra={"video_id": video_id, "platform": platform, "post_id": pid})
        done[platform] = pid
    return Published(done, failures)


def post(
    conn: sqlite3.Connection,
    video_id: str,
    names: Sequence[str] | None,
    env: Mapping[str, str],
    ctx: Context,
) -> str:
    """`reel-bot publish`: post to the named platforms, by default every signed-in one.
    Returns one line per post; raises PublishError listing what failed."""
    every = uploaders(env, ctx)
    names = names or [n for n in load_tokens(ctx.tokens) if n in every]
    if not names:
        raise PublishError("no platform signed in; run reel-bot auth <platform>")
    done = make(conn, video_id, {n: every[n] for n in names}, ctx)
    lines = [f"posted\t{name}\t{pid}" for name, pid in done.posts.items()]
    if done.failures:
        raise PublishError("\n".join([*lines, *done.failures]))
    return "\n".join(lines)
