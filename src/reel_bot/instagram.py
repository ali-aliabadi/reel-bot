"""Instagram Reels through the Instagram API with Instagram Login.

A Meta app in development mode may post to accounts that have a role on it
(ali's account as Instagram Tester), with no App Review, and posts are public.
`reel-bot auth instagram` saves the long-lived token from the app dashboard;
each post refreshes it once it is a day old, so it never reaches its 60-day
expiry while videos keep going out. The video goes up from disk (resumable
upload), so nothing has to be hosted on a public URL.

The API has no AI-content label; the caption carries the disclosure.
"""

from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from reel_bot.publish import Post, PublishError, ident, obj, text
from reel_bot.web import Net, Request

GRAPH = "https://graph.instagram.com/v23.0"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/v23.0"
REFRESH_AFTER = timedelta(days=1)
TOKEN_MAX = 1000
POLLS = 60
POLL_S = 10.0  # up to ten minutes for Instagram to process a video
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def sign_in(net: Net, token: str, now: datetime) -> dict[str, str]:
    """Check a pasted token and return the sign-in to save."""
    what = "Instagram sign-in"
    if not token or len(token) > TOKEN_MAX or not token.isprintable():
        raise PublishError(f"{what}: that does not look like an access token")
    url = f"{GRAPH}/me?" + urlencode({"fields": "user_id,username", "access_token": token})
    me = obj(net.send(Request("GET", url, what)).body, what)
    user = ident(text(me, "user_id", what=what), what)
    return {"access_token": token, "user_id": user, "saved_at": now.isoformat()}


def access(net: Net, saved: dict[str, str], now: datetime) -> tuple[str, dict[str, str]]:
    what = "Instagram token"
    token = text(saved, "access_token", what=what)
    try:
        saved_at = datetime.fromisoformat(text(saved, "saved_at", what=what))
        fresh_enough = now - saved_at < REFRESH_AFTER
    except (ValueError, TypeError) as err:
        raise PublishError(f"{what}: the saved sign-in is damaged; run reel-bot auth") from err
    if fresh_enough:
        return token, saved
    url = REFRESH_URL + "?" + urlencode({"grant_type": "ig_refresh_token", "access_token": token})
    fresh = text(obj(net.send(Request("GET", url, what)).body, what), "access_token", what=what)
    return fresh, saved | {"access_token": fresh, "saved_at": now.isoformat()}


def upload(net: Net, token: str, saved: dict[str, str], video: Path, post: Post) -> str:
    user = ident(text(saved, "user_id", what="Instagram"), "Instagram")
    data = video.read_bytes()
    what = "Instagram reel"
    fields = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": post.caption(),
        "access_token": token,
    }
    made = net.send(
        Request("POST", f"{GRAPH}/{user}/media", what, urlencode(fields).encode(), FORM)
    )
    box = ident(text(obj(made.body, what), "id", what=what), what)
    headers = {"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}
    net.send(Request("POST", f"{RUPLOAD}/{box}", "Instagram upload", data, headers, retry=False))
    _wait(net, token, box)
    body = urlencode({"creation_id": box, "access_token": token}).encode()
    url = f"{GRAPH}/{user}/media_publish"
    done = net.send(Request("POST", url, what, body, FORM, retry=False))
    return ident(text(obj(done.body, what), "id", what=what), what)


def _wait(net: Net, token: str, box: str) -> None:
    what = "Instagram processing"
    url = f"{GRAPH}/{box}?" + urlencode({"fields": "status_code", "access_token": token})
    for _ in range(POLLS):
        status = text(obj(net.send(Request("GET", url, what)).body, what), "status_code", what=what)
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram could not process the video ({status})")
        net.wait(POLL_S)
    raise PublishError("Instagram took over ten minutes to process the video")
