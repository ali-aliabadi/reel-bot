import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import script, store, store_videos, videos, visuals, visuals_write
from reel_bot.script import Paragraph, Script
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video
from reel_bot.visuals_write import Made

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
PNG = visuals.PNG_MAGIC + b"picture"
JPEG = b"\xff\xd8\xff\xe0photo"


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = store.open_db(tmp_path / "reel-bot.db")
    store.sync_stories(c, [FOX])
    return c


def scripted(conn: sqlite3.Connection, out: Path, with_script: bool = True) -> Video:
    v = Video(videos.new_id(NOW), NOW, "fable", "short")
    store_videos.add_video(conn, v)
    if with_script:
        store_videos.choose_story(conn, v.id, FOX.id, "ali", NOW)
        s = Script(
            v.id, FOX.id, FOX.title, "A fox wanted grapes.",
            tuple(Paragraph(f"Scene {i}.", f"A fox, scene {i}.") for i in range(1, 4)),
            "", FOX.source, FOX.source_ref,
        )  # fmt: skip
        folder = videos.video_dir(out, v.id)
        (folder / "script.json").write_text(script.to_json(s), encoding="utf-8")
        store_videos.record_stage(conn, v.id, StageRun("script", "script.json", 0.01, 1, NOW))
    return v


def made(data: bytes = PNG) -> Made:
    return Made(data, "tara", NOW)


def test_adds_pictures_as_they_come_then_finishes(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    assert visuals_write.finish(conn, out, v.id, Clock()) == [1, 2, 3]
    visuals_write.add(conn, out, v.id, 2, made(JPEG))
    visuals_write.add(conn, out, v.id, 1, made())
    assert visuals_write.finish(conn, out, v.id, Clock()) == [3]
    assert "visuals" not in store_videos.stage_runs(conn, v.id)
    visuals_write.add(conn, out, v.id, 3, made())
    assert visuals_write.finish(conn, out, v.id, Clock()) == []
    assert (out / v.id / "picture-2.jpg").read_bytes() == JPEG
    manifest = json.loads((out / v.id / "visuals.json").read_text(encoding="utf-8"))
    assert [(p["section"], p["path"]) for p in manifest["pictures"]] == [
        ("p1", "picture-1.png"), ("p2", "picture-2.jpg"), ("p3", "picture-3.png"),
    ]  # fmt: skip
    assert "A fox, scene 2." in manifest["pictures"][1]["prompt"]
    assert store_videos.stage_runs(conn, v.id)["visuals"].cost_usd == 0
    assets = store_videos.assets(conn, v.id)
    assert [(a.path, a.kind, a.source) for a in assets][1] == (
        "picture-2.jpg", "image", "made in ChatGPT by tara",
    )  # fmt: skip
    assert "commercial use" in assets[0].licence


def test_a_new_picture_replaces_the_old_and_redoes_the_render(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    for n in (1, 2, 3):
        visuals_write.add(conn, out, v.id, n, made())
    visuals_write.finish(conn, out, v.id, Clock())
    store_videos.record_stage(conn, v.id, StageRun("render", "render.mp4", 0, 1, NOW))
    visuals_write.add(conn, out, v.id, 1, made(JPEG))
    assert not (out / v.id / "picture-1.png").exists()
    assert {"visuals", "render"}.isdisjoint(store_videos.stage_runs(conn, v.id))
    assert visuals_write.finish(conn, out, v.id, Clock()) == []


def test_refuses_odd_pictures_numbers_and_a_video_without_a_script(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    with pytest.raises(visuals.VisualsError, match="not a PNG"):
        visuals_write.add(conn, out, v.id, 1, made(b"<html>"))
    assert list((out / v.id).glob("picture-*")) == []
    for n in (0, 4):
        with pytest.raises(visuals.VisualsError, match="pictures 1 to 3"):
            visuals_write.add(conn, out, v.id, n, made())
    bare = scripted(conn, out, with_script=False)
    with pytest.raises(visuals.VisualsError, match="no script"):
        visuals_write.finish(conn, out, bare.id, Clock())
