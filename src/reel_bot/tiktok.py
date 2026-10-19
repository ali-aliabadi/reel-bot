"""TikTok through the Content Posting API (Direct Post).

Until TikTok audits the app, posts can only be private (SELF_ONLY) and the
account must be private while posting; after the audit set
TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE. Every post is marked as AI-generated
content (is_aigc). TikTok may rotate the refresh token on each use, so
`access` returns the new sign-in and the caller saves it before uploading.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

from reel_bot.publish import Post, PublishError, https_on, ident, obj, text
from reel_bot.web import Net, Request

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPE = "video.publish"
PRIVACY = ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE")
INIT = "/post/publish/video/init/"  # creates the post: never retried
MAX_BYTES = 64 << 20  # ponytail: one upload chunk; chunk the file if videos ever pass 64 MB
POLLS = 60
POLL_S = 10.0
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@dataclass(frozen=True)
class App:
    client_key: str
    client_secret: str = field(repr=False)
    redirect_uri: str = ""  # registered on the app; the page itself need not exist
    privacy: str = "SELF_ONLY"

    def check(self) -> None:
        if not (self.client_key and self.client_secret and self.redirect_uri):
            raise PublishError(
                "TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET and TIKTOK_REDIRECT_URI must be set"
            )
        if self.privacy not in PRIVACY:
            raise PublishError(f"TIKTOK_PRIVACY must be one of {', '.join(PRIVACY)}")


def sign_in_url(app: App, state: str) -> str:
    app.check()
    query = {
        "client_key": app.client_key,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": app.redirect_uri,
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(query)


def _token(net: Net, app: App, fields: dict[str, str], what: str) -> dict[str, str]:
    """Never retried: a refresh may rotate the refresh token, and a lost reply would lose it."""
    body = urlencode({"client_key": app.client_key, "client_secret": app.client_secret} | fields)
    req = Request("POST", f"{API}/oauth/token/", what, body.encode(), FORM, retry=False)
    reply = obj(net.send(req).body, what)
    return {
        "access_token": text(reply, "access_token", what=what),
        "refresh_token": text(reply, "refresh_token", what=what),
    }


def exchange(net: Net, app: App, code: str) -> dict[str, str]:
    """Trade the sign-in code for the refresh token to save."""
    fields = {"code": code, "grant_type": "authorization_code", "redirect_uri": app.redirect_uri}
    return {"refresh_token": _token(net, app, fields, "TikTok sign-in")["refresh_token"]}


def access(net: Net, app: App, saved: dict[str, str]) -> tuple[str, dict[str, str]]:
    app.check()
    what = "TikTok token"
    refresh = text(saved, "refresh_token", what=what)
    got = _token(net, app, {"grant_type": "refresh_token", "refresh_token": refresh}, what)
    return got["access_token"], {"refresh_token": got["refresh_token"]}


def _call(net: Net, token: str, path: str, payload: object, what: str) -> dict[str, object]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    req = Request("POST", API + path, what, json.dumps(payload).encode(), headers, path != INIT)
    reply = obj(net.send(req).body, what)
    code = text(reply, "error", "code", what=what)
    if code != "ok":
        raise PublishError(f"{what}: TikTok said {code[:80]}")
    data = reply.get("data")
    if not isinstance(data, dict):
        raise PublishError(f"{what}: the reply has no data")
    return cast(dict[str, object], data)


def upload(net: Net, app: App, token: str, video: Path, post: Post) -> str:
    info = _call(net, token, "/post/publish/creator_info/query/", {}, "TikTok account")
    options = info.get("privacy_level_options")
    if not isinstance(options, list) or app.privacy not in options:
        raise PublishError(f"TikTok does not offer {app.privacy} for this account")
    data = video.read_bytes()
    if len(data) > MAX_BYTES:
        raise PublishError(f"the video is over {MAX_BYTES >> 20} MB, TikTok's single-chunk limit")
    what = "TikTok post"
    payload = {
        "post_info": {
            "title": post.caption(),
            "privacy_level": app.privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "is_aigc": True,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": len(data),
            "chunk_size": len(data),
            "total_chunk_count": 1,
        },
    }
    init = _call(net, token, INIT, payload, what)
    publish_id = ident(text(init, "publish_id", what=what), what)
    url = https_on(text(init, "upload_url", what=what), "tiktokapis.com", what)
    headers = {"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}"}
    net.send(Request("PUT", url, "TikTok upload", data, headers, retry=False))
    _wait(net, token, publish_id)
    return publish_id


def _wait(net: Net, token: str, publish_id: str) -> None:
    what = "TikTok processing"
    for _ in range(POLLS):
        got = _call(net, token, "/post/publish/status/fetch/", {"publish_id": publish_id}, what)
        status = text(got, "status", what=what)
        if status == "PUBLISH_COMPLETE":
            return
        if status == "FAILED":
            reason = got.get("fail_reason")
            raise PublishError(f"TikTok could not post the video ({str(reason)[:80]})")
        net.wait(POLL_S)
    raise PublishError("TikTok took over ten minutes to post the video")
