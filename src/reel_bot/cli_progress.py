"""Live bot status service, independent of the heavy daily worker.

python -m reel_bot.cli_progress [--once] [--video-id VID]
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime

from reel_bot import (
    config,
    logs,
    progress_live,
    relay_api,
    relay_progress,
    script,
    store_progress,
    videos,
    web,
)

INTERVAL_S = 20
log = logging.getLogger(__name__)
ERRORS = (
    OSError,
    ValueError,
    sqlite3.Error,
    relay_api.RelayError,
    web.WebError,
    script.ScriptError,
    videos.VideoError,
)


def tick(
    observer: progress_live.Observer, conn: sqlite3.Connection, video_id: str | None = None
) -> None:
    for vid in [video_id] if video_id else store_progress.recent(conn):
        try:
            observer.update(conn, vid, datetime.now(UTC), force=video_id is not None)
        except ERRORS:
            # Updates are optional: never change/fail the video or log human content.
            log.warning("progress update deferred", extra={"video_id": vid})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--video-id")
    args = parser.parse_args()
    logs.setup("info")
    try:
        cfg = config.load(os.environ)
        narrator = os.environ.get("REEL_BOT_NARRATOR", "").strip()
        picker = os.environ.get("RELAY_USER", "").strip()
        if not narrator or not picker:
            print("config: RELAY_USER and REEL_BOT_NARRATOR are required", file=sys.stderr)
            return 2
        relay = relay_progress.ProgressRelay(
            cfg.relay_url, cfg.relay_api_key, cfg.relay_app, web.Web(8)
        )
        observer = progress_live.Observer(
            relay, cfg.out_dir, (picker, narrator), config.narration(os.environ)
        )
        with store_progress.connect(cfg.db_path) as conn:
            waiting = False
            while True:
                try:
                    if relay.supports_edit():
                        tick(observer, conn, args.video_id)
                        waiting = False
                    elif not waiting:
                        log.info("progress waiting for Relay text editing")
                        waiting = True
                except (web.WebError, relay_api.RelayError):
                    log.warning("progress transport unavailable")
                if args.once:
                    return 0
                time.sleep(INTERVAL_S)
    except config.ConfigError as err:
        print(str(err), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
