"""YouTube: an OAuth sign-in once (`reel-bot auth youtube`), then one resumable
upload per video.

Until Google audits the API project, YouTube locks every API upload as private
whatever the request asks (videos.insert docs); after the audit the upload goes
out as YOUTUBE_PRIVACY (default public). Every upload is marked as containing
AI-made media (the AI disclosure rule).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

from reel_bot.publish import Post, PublishError, https_on, ident, obj, text
from reel_bot.web import Net, Request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (a URL)
UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
)
REDIRECT = "http://127.0.0.1:8765/"  # a "Desktop app" client; nothing needs to listen there
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CATEGORY = "1"  # Film & Animation
PRIVACY = ("public", "unlisted", "private")
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@dataclass(frozen=True)
class App:
    client_id: str
    client_secret: str = field(repr=False)
    privacy: str = "public"

    def check(self) -> None:
        if not (self.client_id and self.client_secret):
            raise PublishError("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be set in .env")
        if self.privacy not in PRIVACY:
            raise PublishError(f"YOUTUBE_PRIVACY must be one of {', '.join(PRIVACY)}")


def sign_in_url(app: App, state: str) -> str:
    app.check()
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": app.client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
    )


def exchange(net: Net, app: App, code: str) -> dict[str, str]:
    """Trade the sign-in code for the refresh token to save."""
    what = "YouTube sign-in"
    body = urlencode(
        {
            "code": code,
            "client_id": app.client_id,
            "client_secret": app.client_secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        }
    )
    reply = net.send(Request("POST", TOKEN_URL, what, body.encode(), FORM, retry=False))
    return {"refresh_token": text(obj(reply.body, what), "refresh_token", what=what)}


def access(net: Net, app: App, saved: dict[str, str]) -> tuple[str, dict[str, str]]:
    app.check()
    what = "YouTube token"
    body = urlencode(
        {
            "client_id": app.client_id,
            "client_secret": app.client_secret,
            "refresh_token": text(saved, "refresh_token", what=what),
            "grant_type": "refresh_token",
        }
    )
    reply = net.send(Request("POST", TOKEN_URL, what, body.encode(), FORM))
    return text(obj(reply.body, what), "access_token", what=what), saved


def upload(net: Net, app: App, token: str, video: Path, post: Post) -> str:
    data = video.read_bytes()  # ponytail: whole file in memory; fine for a ~20 MB short
    meta = {
        "snippet": {"title": post.title, "description": post.description, "categoryId": CATEGORY},
        "status": {
            "privacyStatus": app.privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    auth = {"Authorization": f"Bearer {token}"}
    start = net.send(
        Request(
            "POST",
            UPLOAD_URL,
            "YouTube upload start",
            json.dumps(meta).encode(),
            auth
            | {
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(len(data)),
            },
        )
    )
    where = https_on(start.headers.get("location", ""), "googleapis.com", "YouTube upload start")
    put = Request("PUT", where, "YouTube upload", data, auth | {"Content-Type": "video/mp4"}, False)
    done = net.send(put)
    return ident(text(obj(done.body, "YouTube upload"), "id", what="YouTube upload"), "YouTube")
