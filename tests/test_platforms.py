"""The publish core and the three platform clients against a scripted fake network.
Nothing here talks to YouTube, Instagram or TikTok."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from reel_bot import instagram, publish, tiktok, youtube
from reel_bot.publish import Post, PublishError
from reel_bot.script import Paragraph, Script
from reel_bot.videos import StageRun, Video
from reel_bot.web import Reply, Request

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
POST = Post("The Fox and the Grapes #shorts", "A hungry fox.\n\nPictures made with AI.")
YT = youtube.App("cid", "csecret")
TT = tiktok.App("ckey", "csecret", "https://example.test/tiktok")


def ok(body: object, headers: dict[str, str] | None = None) -> Reply:
    return Reply(200, headers or {}, json.dumps(body).encode())


def tt(data: object, code: str = "ok") -> Reply:
    return ok({"data": data, "error": {"code": code, "message": ""}})


class FakeNet:
    """Replies in order; each must match the expected method and URL start."""

    def __init__(self, *script: tuple[str, str, Reply]) -> None:
        self.script = list(script)
        self.sent: list[Request] = []
        self.waits: list[float] = []

    def send(self, req: Request) -> Reply:
        self.sent.append(req)
        method, start, reply = self.script.pop(0)
        assert (req.method, req.url[: len(start)]) == (method, start)
        return reply

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture
def mp4(tmp_path: Path) -> Path:
    path = tmp_path / "render.mp4"
    path.write_bytes(b"\x00" * 1000)
    return path


def form(req: Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(req.body.decode()).items()}


# The pure core


def make_script(title: str = "The Fox and the Grapes") -> Script:
    para = Paragraph("He jumped.", "A fox.")
    return Script("vid_X", "aesop-fox", title, "A hungry fox.", (para,), "", "Townsend", "p. 1")


def test_post_names_the_source_and_discloses_ai() -> None:
    post = publish.post_for(make_script("The <Fox>"))
    assert post.title == "The Fox #shorts"
    assert "Retold from Townsend, p. 1" in post.description
    assert "made with AI" in post.description
    assert post.caption().startswith("The Fox #shorts\n\nA hungry fox.")


def test_long_titles_fit_youtube() -> None:
    post = publish.post_for(make_script("x" * 300))
    assert len(post.title) <= publish.TITLE_MAX
    assert post.title.endswith("… #shorts")
    assert len(Post("t", "d" * 5000).caption()) == publish.CAPTION_MAX


def test_only_a_picked_rendered_video_is_ready() -> None:
    bare = Video("vid_X", NOW, "fable", "short")
    picked = Video("vid_X", NOW, "fable", "short", (), "aesop-fox", "ali", NOW)
    render = {"render": StageRun("render", "render.mp4", 0.0, 1.0, NOW)}
    with pytest.raises(PublishError, match="no recorded story pick"):
        publish.check_ready(bare, render)
    with pytest.raises(PublishError, match="no render"):
        publish.check_ready(picked, {})
    publish.check_ready(picked, render)


def test_the_sign_in_code_comes_only_from_this_sign_in() -> None:
    assert publish.code_from(" http://127.0.0.1:8765/?state=s1&code=4/abc ", "s1") == "4/abc"
    with pytest.raises(PublishError, match="state"):
        publish.code_from("http://127.0.0.1:8765/?state=other&code=abc", "s1")
    with pytest.raises(PublishError, match="refused: access_denied"):
        publish.code_from("http://x/?state=s1&error=access_denied", "s1")
    with pytest.raises(PublishError, match="no sign-in code"):
        publish.code_from("http://x/?state=s1", "s1")
    with pytest.raises(PublishError, match="not a web address"):
        publish.code_from("http://[::1", "s1")


def test_upload_urls_must_be_on_the_platform() -> None:
    assert publish.https_on("https://open-upload.tiktokapis.com/x", "tiktokapis.com", "t")
    for bad in ["https://eviltiktokapis.com/x", "http://open.tiktokapis.com/x", "", "https://[x"]:
        with pytest.raises(PublishError):
            publish.https_on(bad, "tiktokapis.com", "t")
    with pytest.raises(PublishError, match="odd ID"):
        publish.ident("../x", "t")


# YouTube


def test_youtube_sign_in_asks_for_upload_only_and_offline_access() -> None:
    query = parse_qs(urlparse(youtube.sign_in_url(YT, "s1")).query)
    assert query["scope"] == [youtube.SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["state"] == ["s1"]
    net = FakeNet(("POST", youtube.TOKEN_URL, ok({"refresh_token": "rt", "access_token": "a"})))
    assert youtube.exchange(net, YT, "code1") == {"refresh_token": "rt"}
    assert form(net.sent[0])["grant_type"] == "authorization_code"


def test_youtube_needs_its_client_and_a_known_privacy() -> None:
    with pytest.raises(PublishError, match="YOUTUBE_CLIENT_ID"):
        youtube.sign_in_url(youtube.App("", ""), "s")
    with pytest.raises(PublishError, match="YOUTUBE_PRIVACY"):
        youtube.App("a", "b", "everyone").check()


def test_youtube_upload_marks_ai_media_and_never_retries_the_upload(mp4: Path) -> None:
    where = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=u1"
    net = FakeNet(
        ("POST", youtube.TOKEN_URL, ok({"access_token": "at"})),
        ("POST", youtube.UPLOAD_URL, ok({}, {"location": where})),
        ("PUT", where, ok({"id": "dQw4w9WgXcQ"})),
    )
    token, saved = youtube.access(net, YT, {"refresh_token": "rt"})
    assert (token, saved) == ("at", {"refresh_token": "rt"})
    assert youtube.upload(net, YT, token, mp4, POST) == "dQw4w9WgXcQ"
    meta = json.loads(net.sent[1].body)
    assert meta["status"] == {
        "privacyStatus": "public",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    assert meta["snippet"]["title"] == POST.title
    assert net.sent[1].headers["X-Upload-Content-Length"] == "1000"
    assert net.sent[2].headers["Authorization"] == "Bearer at"
    assert not net.sent[2].retry


def test_youtube_refuses_an_upload_url_off_google(mp4: Path) -> None:
    net = FakeNet(("POST", youtube.UPLOAD_URL, ok({}, {"location": "https://evil.test/u"})))
    with pytest.raises(PublishError, match=r"not on googleapis\.com"):
        youtube.upload(net, YT, "at", mp4, POST)


# Instagram


def test_instagram_sign_in_checks_the_token() -> None:
    net = FakeNet(("GET", f"{instagram.GRAPH}/me?", ok({"user_id": "1784", "username": "f"})))
    saved = instagram.sign_in(net, "IGtoken", NOW)
    assert saved == {"access_token": "IGtoken", "user_id": "1784", "saved_at": NOW.isoformat()}
    for bad in ["", "x" * 2000, "a\nb"]:
        with pytest.raises(PublishError, match="access token"):
            instagram.sign_in(FakeNet(), bad, NOW)


def test_instagram_token_is_refreshed_once_a_day_old() -> None:
    saved = {"access_token": "old", "user_id": "1784", "saved_at": NOW.isoformat()}
    assert instagram.access(FakeNet(), saved, NOW + timedelta(hours=1)) == ("old", saved)
    net = FakeNet(("GET", instagram.REFRESH_URL, ok({"access_token": "new"})))
    token, fresh = instagram.access(net, saved, NOW + timedelta(days=2))
    assert token == "new"  # noqa: S105
    assert fresh["saved_at"] == (NOW + timedelta(days=2)).isoformat()
    with pytest.raises(PublishError, match="damaged"):
        instagram.access(FakeNet(), saved | {"saved_at": "yesterday"}, NOW)


def test_instagram_posts_a_reel_from_disk(mp4: Path) -> None:
    g = instagram.GRAPH
    net = FakeNet(
        ("POST", f"{g}/1784/media", ok({"id": "1790"})),
        ("POST", f"{instagram.RUPLOAD}/1790", ok({"success": True})),
        ("GET", f"{g}/1790?", ok({"status_code": "IN_PROGRESS"})),
        ("GET", f"{g}/1790?", ok({"status_code": "FINISHED"})),
        ("POST", f"{g}/1784/media_publish", ok({"id": "1791"})),
    )
    saved = {"access_token": "at", "user_id": "1784", "saved_at": NOW.isoformat()}
    assert instagram.upload(net, "at", saved, mp4, POST) == "1791"
    made = form(net.sent[0])
    assert (made["media_type"], made["upload_type"]) == ("REELS", "resumable")
    assert "made with AI" in made["caption"]
    assert net.sent[1].headers == {"Authorization": "OAuth at", "offset": "0", "file_size": "1000"}
    assert net.waits == [instagram.POLL_S]
    assert not net.sent[4].retry
    assert form(net.sent[4])["creation_id"] == "1790"


def test_instagram_stops_on_a_processing_error_or_after_ten_minutes(mp4: Path) -> None:
    saved = {"access_token": "at", "user_id": "1784"}
    start = [
        ("POST", f"{instagram.GRAPH}/1784/media", ok({"id": "1790"})),
        ("POST", instagram.RUPLOAD, ok({})),
    ]
    net = FakeNet(*start, ("GET", instagram.GRAPH, ok({"status_code": "ERROR"})))
    with pytest.raises(PublishError, match="could not process"):
        instagram.upload(net, "at", saved, mp4, POST)
    busy = [("GET", instagram.GRAPH, ok({"status_code": "IN_PROGRESS"}))] * instagram.POLLS
    with pytest.raises(PublishError, match="ten minutes"):
        instagram.upload(FakeNet(*start, *busy), "at", saved, mp4, POST)


# TikTok


def test_tiktok_sign_in_and_token_rotation() -> None:
    query = parse_qs(urlparse(tiktok.sign_in_url(TT, "s1")).query)
    assert (query["scope"], query["redirect_uri"]) == (["video.publish"], [TT.redirect_uri])
    token_url = f"{tiktok.API}/oauth/token/"
    net = FakeNet(
        ("POST", token_url, ok({"access_token": "a1", "refresh_token": "r1"})),
        ("POST", token_url, ok({"access_token": "a2", "refresh_token": "r2"})),
    )
    assert tiktok.exchange(net, TT, "code1") == {"refresh_token": "r1"}
    assert tiktok.access(net, TT, {"refresh_token": "r1"}) == ("a2", {"refresh_token": "r2"})
    assert form(net.sent[1])["grant_type"] == "refresh_token"
    assert not any(r.retry for r in net.sent)
    with pytest.raises(PublishError, match="TIKTOK_REDIRECT_URI"):
        tiktok.App("k", "s").check()
    with pytest.raises(PublishError, match="TIKTOK_PRIVACY"):
        tiktok.App("k", "s", "https://x.test", "PUBLIC").check()


def tiktok_start(options: list[str]) -> list[tuple[str, str, Reply]]:
    info = tt({"privacy_level_options": options, "max_video_post_duration_sec": 600})
    return [("POST", f"{tiktok.API}/post/publish/creator_info/", info)]


def test_tiktok_posts_privately_and_labels_ai(mp4: Path) -> None:
    upload_url = "https://open-upload.tiktokapis.com/video/?upload_id=1"
    net = FakeNet(
        *tiktok_start(["SELF_ONLY", "PUBLIC_TO_EVERYONE"]),
        ("POST", f"{tiktok.API}{tiktok.INIT}", tt({"publish_id": "p1", "upload_url": upload_url})),
        ("PUT", upload_url, Reply(201, {}, b"")),
        ("POST", f"{tiktok.API}/post/publish/status/", tt({"status": "PROCESSING_UPLOAD"})),
        ("POST", f"{tiktok.API}/post/publish/status/", tt({"status": "PUBLISH_COMPLETE"})),
    )
    assert tiktok.upload(net, TT, "at", mp4, POST) == "p1"
    init = json.loads(net.sent[1].body)
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init["post_info"]["is_aigc"] is True
    assert init["source_info"]["video_size"] == 1000
    assert not net.sent[1].retry
    assert net.sent[2].headers["Content-Range"] == "bytes 0-999/1000"
    assert net.waits == [tiktok.POLL_S]


@pytest.mark.parametrize(
    ("replies", "error"),
    [
        (tiktok_start(["FOLLOWER_OF_CREATOR"]), "does not offer SELF_ONLY"),
        ([("POST", tiktok.API, tt({}, "access_token_invalid"))], "said access_token_invalid"),
        ([("POST", tiktok.API, ok({"error": {"code": "ok"}}))], "no data"),
        (
            [
                *tiktok_start(["SELF_ONLY"]),
                ("POST", tiktok.API, tt({"publish_id": "p1", "upload_url": "https://evil.test"})),
            ],
            r"not on tiktokapis\.com",
        ),
        (
            [
                *tiktok_start(["SELF_ONLY"]),
                (
                    "POST",
                    tiktok.API,
                    tt({"publish_id": "p1", "upload_url": "https://open-upload.tiktokapis.com"}),
                ),
                ("PUT", "https://open-upload", Reply(201, {}, b"")),
                ("POST", tiktok.API, tt({"status": "FAILED", "fail_reason": "spam_risk"})),
            ],
            "spam_risk",
        ),
    ],
    ids=["privacy", "error-code", "no-data", "upload-url", "failed"],
)
def test_tiktok_refusals(mp4: Path, replies: list[tuple[str, str, Reply]], error: str) -> None:
    with pytest.raises(PublishError, match=error):
        tiktok.upload(FakeNet(*replies), TT, "at", mp4, POST)


def test_tiktok_refuses_a_video_over_one_chunk(mp4: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tiktok, "MAX_BYTES", 10)
    with pytest.raises(PublishError, match="single-chunk"):
        tiktok.upload(FakeNet(*tiktok_start(["SELF_ONLY"])), TT, "at", mp4, POST)


def test_tiktok_gives_up_after_ten_minutes(mp4: Path) -> None:
    url = "https://open-upload.tiktokapis.com/v"
    busy = [("POST", tiktok.API, tt({"status": "PROCESSING_UPLOAD"}))] * tiktok.POLLS
    net = FakeNet(
        *tiktok_start(["SELF_ONLY"]),
        ("POST", tiktok.API, tt({"publish_id": "p1", "upload_url": url})),
        ("PUT", url, Reply(201, {}, b"")),
        *busy,
    )
    with pytest.raises(PublishError, match="ten minutes"):
        tiktok.upload(net, TT, "at", mp4, POST)
