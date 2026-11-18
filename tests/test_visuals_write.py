import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reel_bot import captions, script, store, store_videos, videos, visuals, visuals_write
from reel_bot.captions import Captions, Cue, Section
from reel_bot.cli import main
from reel_bot.script import Paragraph, Script
from reel_bot.stories import Story
from reel_bot.videos import StageRun, Video
from reel_bot.visuals_write import Made

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FOX = Story("aesop-fox", "fox-and-grapes", "The Fox and the Grapes", "Aesop", "Townsend", "p. 1")
PNG = visuals.PNG_MAGIC + b"picture"
JPEG = b"\xff\xd8\xff\xe0photo"
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; GIF rendering needs them",
)


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


def run_media(binary: str, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        [binary, "-v", "error", *args],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    ).stdout


def short_animation(tmp_path: Path, kind: str) -> Path:
    path = tmp_path / "gemini.download"  # detection should work without a file extension
    encode = ["-loop", "-1"] if kind == "gif" else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    run_media(
        "ffmpeg", "-f", "lavfi", "-i", "nullsrc=s=96x96:r=10,geq=lum='255*mod(N,2)':cb=128:cr=128",
        "-frames:v", "2", *encode, "-f", kind, str(path),
    )  # fmt: skip
    return path


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
    with pytest.raises(visuals.VisualsError, match="the scene needs a PNG"):
        visuals_write.add(conn, out, v.id, 1, made(b"<html>"))
    assert list((out / v.id).glob("picture-*")) == []
    for n in (0, 4):
        with pytest.raises(visuals.VisualsError, match="pictures 1 to 3"):
            visuals_write.add(conn, out, v.id, n, made())
    bare = scripted(conn, out, with_script=False)
    with pytest.raises(visuals.VisualsError, match="no script"):
        visuals_write.finish(conn, out, bare.id, Clock())


@needs_ffmpeg
@pytest.mark.parametrize("kind", ["gif", "mp4"])
def test_cli_renders_short_animation_with_stills_and_keeps_it_moving(
    conn: sqlite3.Connection, tmp_path: Path, kind: str
) -> None:
    animation = short_animation(tmp_path, kind)
    out = tmp_path / "out"
    v = scripted(conn, out)
    folder = out / v.id
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db"), "REEL_BOT_OUT_DIR": str(out)}
    still = tmp_path / "still.png"
    run_media("ffmpeg", "-f", "lavfi", "-i", "color=c=navy:s=96x96", "-frames:v", "1", str(still))
    for n in (1, 2, 3):
        visuals_write.add(conn, out, v.id, n, made(still.read_bytes()))
    assert visuals_write.finish(conn, out, v.id, Clock()) == []
    assert main(["visuals", v.id, "--add", "2", str(animation)], env, Clock()) == 0
    assert not (folder / "picture-2.png").exists()
    scene = folder / f"picture-2.{kind}"
    assert scene.read_bytes() == animation.read_bytes()
    asset = next(a for a in store_videos.assets(conn, v.id) if a.path == scene.name)
    assert (asset.kind, asset.source, asset.licence) == (
        "footage", "made in Gemini by ali", visuals_write.GEMINI_LICENCE,
    )  # fmt: skip

    run_media(
        "ffmpeg", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", str(folder / "voice.wav")
    )
    cap = Captions(
        v.id, (), (Cue("A fox", 0.2, 0.8),),
        (Section("p1", 0, 0.9), Section("p2", 1.1, 2.9), Section("p3", 3.1, 4)),
        1.0,
    )  # fmt: skip
    (folder / "captions.json").write_text(captions.to_json(cap), encoding="utf-8")
    for stage, name in (("voice", "voice.wav"), ("captions", "captions.json")):
        store_videos.record_stage(conn, v.id, StageRun(stage, name, 0, 1, NOW))
    assert main(["render", v.id], env, Clock()) == 0
    mp4 = folder / "render.mp4"
    assert store_videos.stage_runs(conn, v.id)["render"].path == mp4.name
    probe = run_media(
        "ffprobe", "-show_entries", "stream=codec_name,duration", "-of", "json", str(mp4)
    )
    streams = json.loads(probe)["streams"]
    assert [s["codec_name"] for s in streams] == ["h264", "aac"]
    assert [float(s["duration"]) for s in streams] == pytest.approx([4, 4], abs=0.1)
    # Look for hard changes well inside the second scene, after the 0.2 s clip
    # would have ended. The fades and the caption fall outside this interval.
    changes = run_media(
        "ffmpeg", "-i", str(mp4), "-an", "-vf",
        r"scale=96:96,select=between(t\,1.5\,2.5)*gt(scene\,0.4)", "-fps_mode", "passthrough",
        "-f", "framehash", "-",
    )  # fmt: skip
    assert sum(line.startswith("0,") for line in changes.splitlines()) >= 4


@needs_ffmpeg
def test_unreadable_gif_does_not_replace_a_scene_or_invalidate_its_render(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    path = visuals_write.add(conn, out, v.id, 1, made())
    store_videos.record_stage(conn, v.id, StageRun("render", "render.mp4", 0, 1, NOW))
    with pytest.raises(visuals.VisualsError, match="the GIF is unreadable"):
        visuals_write.add(conn, out, v.id, 1, made(b"GIF89a\x3b"))
    assert path.read_bytes() == PNG
    assert sorted(p.name for p in path.parent.iterdir()) == ["picture-1.png", "script.json"]
    assert "render" in store_videos.stage_runs(conn, v.id)


@needs_ffmpeg
def test_mp4_with_only_audio_is_rejected(conn: sqlite3.Connection, tmp_path: Path) -> None:
    out = tmp_path / "out"
    v = scripted(conn, out)
    clip = tmp_path / "audio.mp4"
    run_media("ffmpeg", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.2", str(clip))
    with pytest.raises(visuals.VisualsError, match="the MP4 is unreadable"):
        visuals_write.add(conn, out, v.id, 1, made(clip.read_bytes()))
    assert list((out / v.id).glob("picture-*")) == []
