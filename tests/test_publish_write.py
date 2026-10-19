"""Posting a video: the pick gate, one post per platform, reruns after a failure,
sign-ins saved owner-only, and the CLI. Uploads are fakes; nothing is posted."""

import json
import sqlite3
import stat
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from reel_bot import publish_write, script, store, store_videos, stories, web
from reel_bot.cli import main
from reel_bot.publish import Post, PublishError, Uploader
from reel_bot.publish_write import Context
from reel_bot.script import Paragraph
from reel_bot.videos import StageRun, Video
from reel_bot.web import Reply, Request

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
VID = "vid_01K8ABCDEFGHJKMNPQRSTVWXYZ"


class NoNet:
    def send(self, req: Request) -> Reply:
        raise AssertionError(f"unexpected call: {req.what}")

    def wait(self, seconds: float) -> None:
        raise AssertionError("unexpected wait")


class OneReply(NoNet):
    def __init__(self, body: object) -> None:
        self.body = body
        self.sent: list[Request] = []

    def send(self, req: Request) -> Reply:
        self.sent.append(req)
        return Reply(200, {}, json.dumps(self.body).encode())


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    return Context(tmp_path / "out", tmp_path / "data" / "tokens.json", lambda: NOW, NoNet())


@pytest.fixture
def conn(tmp_path: Path, ctx: Context) -> Iterator[sqlite3.Connection]:
    """A picked, rendered video VID."""
    c = store.open_db(tmp_path / "data" / "reel-bot.db")
    store.sync_stories(c, stories.load_builtin())
    story = store.all_stories(c)[0]
    store_videos.add_video(c, Video(VID, NOW, "fable", "short"))
    store_videos.choose_story(c, VID, story.id, "ali", NOW)
    folder = ctx.out_dir / VID
    folder.mkdir(parents=True)
    s = script.Script(VID, story.id, story.title, "Hook.", (Paragraph("p", "q"),), "", "S", "R")
    (folder / "script.json").write_text(script.to_json(s), encoding="utf-8")
    (folder / "render.mp4").write_bytes(b"mp4")
    store_videos.record_stage(c, VID, StageRun("render", "render.mp4", 0.0, 1.0, NOW))
    yield c
    c.close()


class Fake:
    """An uploader that records its posts; `fail` makes the upload raise."""

    def __init__(self, name: str, fail: bool = False) -> None:
        self.name, self.fail = name, fail
        self.posted: list[Post] = []

    def uploader(self) -> Uploader:
        def access(saved: dict[str, str]) -> tuple[str, dict[str, str]]:
            return "token", saved | {"rotated": "yes"}

        def upload(token: str, saved: dict[str, str], mp4: Path, post: Post) -> str:
            assert mp4.read_bytes() == b"mp4"
            if self.fail:
                raise PublishError("quota")
            self.posted.append(post)
            return f"{self.name}-1"

        return Uploader(access, upload)


def sign_in_all(ctx: Context) -> None:
    for name in ("youtube", "instagram", "tiktok"):
        publish_write.save_tokens(ctx.tokens, name, {"refresh_token": "r"})


def test_posts_everywhere_once_and_reruns_only_where_it_failed(
    conn: sqlite3.Connection, ctx: Context
) -> None:
    sign_in_all(ctx)
    yt, ig, tt = Fake("yt"), Fake("ig"), Fake("tt", fail=True)
    to = {"youtube": yt.uploader(), "instagram": ig.uploader(), "tiktok": tt.uploader()}
    done = publish_write.make(conn, VID, to, ctx)
    assert done.posts == {"youtube": "yt-1", "instagram": "ig-1"}
    assert done.failures == ["tiktok: quota"]
    assert "made with AI" in yt.posted[0].description
    assert store_videos.posts(conn, VID) == done.posts
    # the rotated sign-in was saved even though the tiktok upload failed
    assert publish_write.load_tokens(ctx.tokens)["tiktok"]["rotated"] == "yes"

    tt.fail = False
    again = publish_write.make(conn, VID, to, ctx)
    assert again.posts == {"youtube": "yt-1", "instagram": "ig-1", "tiktok": "tt-1"}
    assert (len(yt.posted), len(ig.posted), len(tt.posted)) == (1, 1, 1)


def test_a_platform_without_a_sign_in_is_reported(conn: sqlite3.Connection, ctx: Context) -> None:
    done = publish_write.make(conn, VID, {"youtube": Fake("yt").uploader()}, ctx)
    assert done.failures == ["youtube: not signed in (run reel-bot auth youtube)"]


def test_nothing_is_posted_without_a_pick(conn: sqlite3.Connection, ctx: Context) -> None:
    sign_in_all(ctx)
    other = "vid_01K8ABCDEFGHJKMNPQRSTVWXY0"
    store_videos.add_video(conn, Video(other, NOW, "fable", "short"))
    yt = Fake("yt")
    with pytest.raises(PublishError, match="no recorded story pick"):
        publish_write.make(conn, other, {"youtube": yt.uploader()}, ctx)
    assert yt.posted == []
    with pytest.raises(store_videos.PickError, match="no recorded story pick"):
        store_videos.add_post(conn, other, "youtube", "x", NOW)  # the database refuses too


def test_sign_ins_are_owner_only_and_damage_is_reported(ctx: Context) -> None:
    assert publish_write.load_tokens(ctx.tokens) == {}
    publish_write.save_tokens(ctx.tokens, "youtube", {"refresh_token": "r"})
    publish_write.save_tokens(ctx.tokens, "tiktok", {"refresh_token": "t"})
    assert stat.S_IMODE(ctx.tokens.stat().st_mode) == 0o600
    assert set(publish_write.load_tokens(ctx.tokens)) == {"youtube", "tiktok"}
    for damaged in ["{", "[1]"]:
        ctx.tokens.write_text(damaged, encoding="utf-8")
        with pytest.raises(PublishError, match="damaged"):
            publish_write.load_tokens(ctx.tokens)


ENV = {
    "YOUTUBE_CLIENT_ID": "cid",
    "YOUTUBE_CLIENT_SECRET": "cs",
    "TIKTOK_CLIENT_KEY": "ck",
    "TIKTOK_CLIENT_SECRET": "cs",
    "TIKTOK_REDIRECT_URI": "https://example.test/tiktok",
}


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_oauth_sign_in_saves_the_refresh_token(ctx: Context, platform: str) -> None:
    net = OneReply({"access_token": "a", "refresh_token": "r1"})
    ctx = Context(ctx.out_dir, ctx.tokens, ctx.clock, net)

    def ask(prompt: str, hidden: bool) -> str:
        assert not hidden
        url = next(line for line in prompt.splitlines() if line.startswith("https://"))
        state = parse_qs(urlparse(url).query)["state"][0]
        return f"http://127.0.0.1:8765/?state={state}&code=c1"

    publish_write.sign_in(platform, ENV, ctx, ask)
    assert publish_write.load_tokens(ctx.tokens) == {platform: {"refresh_token": "r1"}}
    assert "code=c1" in net.sent[0].body.decode()


def test_instagram_sign_in_reads_the_token_hidden(ctx: Context) -> None:
    net = OneReply({"user_id": "1784"})
    ctx = Context(ctx.out_dir, ctx.tokens, ctx.clock, net)
    asked: list[bool] = []

    def ask(prompt: str, hidden: bool) -> str:
        asked.append(hidden)
        return " IGtoken \n"

    publish_write.sign_in("instagram", {}, ctx, ask)
    assert asked == [True]
    assert publish_write.load_tokens(ctx.tokens)["instagram"]["access_token"] == "IGtoken"  # noqa: S105


def test_post_defaults_to_signed_in_platforms(conn: sqlite3.Connection, ctx: Context) -> None:
    with pytest.raises(PublishError, match="no platform signed in"):
        publish_write.post(conn, VID, None, ENV, ctx)
    publish_write.save_tokens(ctx.tokens, "youtube", {"refresh_token": "r"})
    with pytest.raises(
        PublishError, match="youtube: YouTube token: the reply is not a JSON object"
    ):
        publish_write.post(
            conn, VID, None, ENV, Context(ctx.out_dir, ctx.tokens, ctx.clock, OneReply(""))
        )


def test_cli_publish_and_auth_refuse_cleanly(
    tmp_path: Path, conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {
        "REEL_BOT_DB_PATH": str(tmp_path / "data" / "reel-bot.db"),
        "REEL_BOT_OUT_DIR": str(tmp_path / "out"),
    }
    assert main(["publish", VID], env, lambda: NOW) == 3
    assert "no platform signed in" in capsys.readouterr().err
    assert main(["auth", "youtube"], env, lambda: NOW) == 3
    assert "YOUTUBE_CLIENT_ID" in capsys.readouterr().err


def test_platform_error_codes_reach_the_message_but_bodies_do_not() -> None:
    hint = web._hint  # pyright: ignore[reportPrivateUsage]
    assert hint(b'{"error": {"code": "spam_risk", "message": "secret-ish"}}') == " (spam_risk)"
    assert hint(b'{"error": "invalid_grant", "error_description": "x"}') == " (invalid_grant)"
    assert hint(b'{"error": {"code": 190, "type": "OAuthException"}}') == " (OAuthException)"
    for odd in [b"", b"[", b"[1]", b'{"error": "has spaces"}', b"[" * 100_000]:
        assert hint(odd) == ""
    with pytest.raises(web.WebError, match="only https"):
        web.Web().send(Request("GET", "http://example.test/", "test"))


class _Platform(BaseHTTPRequestHandler):
    """/ok answers 200 with a Location; /<code> answers that status with an error code."""

    def do_GET(self) -> None:
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Location", "https://www.googleapis.com/u")
            self.end_headers()
            self.wfile.write(b'{"id": "1"}')
            return
        self.send_response(int(self.path[1:]))
        self.end_headers()
        self.wfile.write(b'{"error": {"code": "spam_risk", "message": "not shown"}}')

    def log_message(self, format: str, *args: Any) -> None:
        pass


def test_web_reads_replies_and_retries_only_busy_answers() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Platform)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    slept: list[float] = []
    net = web.Web(timeout_s=5, sleep=slept.append)
    try:
        reply = net.send(Request("GET", base + "/ok", "test"))
        assert (reply.status, reply.headers["location"], reply.body) == (
            200,
            "https://www.googleapis.com/u",
            b'{"id": "1"}',
        )
        with pytest.raises(web.WebError, match=r"^test failed: HTTP 400 \(spam_risk\)$"):
            net.send(Request("GET", base + "/400", "test"))
        assert slept == []
        with pytest.raises(web.WebError, match="HTTP 503"):
            net.send(Request("GET", base + "/503", "test"))
        assert slept == [2.0, 4.0]
    finally:
        server.shutdown()
