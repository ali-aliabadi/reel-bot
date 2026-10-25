from datetime import UTC, datetime
from pathlib import Path

import pytest

from reel_bot import __version__, script, store, store_videos, videos
from reel_bot.cli import main
from reel_bot.script import Paragraph, Script
from reel_bot.videos import StageRun

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)


def test_version_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_config_shows_settings_without_the_key(capsys: pytest.CaptureFixture[str]) -> None:
    env = {"RELAY_URL": "https://relay.example.test", "RELAY_API_KEY": "rk_test_not_a_real_key"}
    assert main(["config"], env=env) == 0
    out = capsys.readouterr().out
    assert "RELAY_URL=https://relay.example.test" in out
    assert "RELAY_API_KEY=set" in out
    assert "rk_test_not_a_real_key" not in out


def test_config_lists_problems_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"], env={}) == 2
    err = capsys.readouterr().err
    assert "RELAY_URL is required" in err
    assert "RELAY_API_KEY is required" in err


def test_stories_use_refuses_a_tale_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db")}
    assert main(["stories", "list"], env, lambda: datetime(2026, 10, 22, tzinfo=UTC)) == 0
    assert "kalilah-lion-and-hare" in capsys.readouterr().out

    assert (
        main(
            ["stories", "use", "kalilah-lion-and-hare", "vid_1"],
            env,
            lambda: datetime(2026, 10, 22, tzinfo=UTC),
        )
        == 0
    )
    assert (
        main(
            ["stories", "use", "panchatantra-lion-and-hare", "vid_2"],
            env,
            lambda: datetime(2026, 10, 22, tzinfo=UTC),
        )
        == 3
    )
    assert "already told in video vid_1" in capsys.readouterr().err

    assert main(["stories"], env, lambda: datetime(2026, 10, 22, tzinfo=UTC)) == 0
    assert "lion-and-hare" not in capsys.readouterr().out


def test_stories_pick(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db")}
    assert main(["stories", "pick", "-n", "2"], env) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_bad_log_level_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"], env={"REEL_BOT_LOG_LEVEL": "loud"}) == 2
    assert "REEL_BOT_LOG_LEVEL" in capsys.readouterr().err


def test_video_new_choose_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db")}

    def clock() -> datetime:
        return datetime(2026, 10, 22, 9, tzinfo=UTC)

    assert main(["video", "new", "--framing", "lesson", "--length", "long"], env, clock) == 0
    vid = capsys.readouterr().out.strip()
    assert vid.startswith("vid_")
    pick = ["video", "choose", vid, "aesop-fox-and-grapes", "--by", "ali"]
    assert main(pick, env, clock) == 0
    assert main(["video", "show", vid], env, clock) == 0
    out = capsys.readouterr().out
    assert "framing=lesson" in out
    assert "story=aesop-fox-and-grapes" in out
    assert "chosen by ali at 2026-10-22T09:00:00+00:00" in out
    assert main(pick, env, clock) == 3
    assert "already has its story" in capsys.readouterr().err


def test_video_show_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db")}
    assert main(["video", "show", "vid_nope"], env) == 3
    assert "no video" in capsys.readouterr().err


def test_script_refuses_a_video_without_a_pick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_OUT_DIR": str(tmp_path / "out")}
    assert main(["video", "new", "--framing", "fable", "--length", "short"], env) == 0
    vid = capsys.readouterr().out.strip()
    assert main(["script", vid], env) == 3
    assert "no picked story" in capsys.readouterr().err


def test_voice_refuses_before_the_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_OUT_DIR": str(tmp_path / "out")}
    assert main(["video", "new", "--framing", "fable", "--length", "short"], env) == 0
    vid = capsys.readouterr().out.strip()
    assert main(["voice", vid, str(tmp_path / "take.ogg")], env) == 3
    assert "no script" in capsys.readouterr().err


def test_captions_needs_the_whisper_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no models/ here
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_OUT_DIR": str(tmp_path / "out")}
    assert main(["video", "new", "--framing", "fable", "--length", "short"], env) == 0
    vid = capsys.readouterr().out.strip()
    assert main(["captions", vid], env) == 3
    assert "make models" in capsys.readouterr().err


def test_visuals_lists_missing_prompts_and_adds_pictures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_OUT_DIR": str(tmp_path / "out")}
    assert main(["video", "new", "--framing", "fable", "--length", "short"], env) == 0
    vid = capsys.readouterr().out.strip()
    assert main(["visuals", vid], env) == 3
    assert "no script" in capsys.readouterr().err
    conn = store.open_db(tmp_path / "db")
    story = store.all_stories(conn)[0]
    store_videos.choose_story(conn, vid, story.id, "ali", NOW)
    paragraphs = (Paragraph("One.", "A fox."), Paragraph("Two.", "A vine."))
    s = Script(vid, story.id, story.title, "Hook.", paragraphs, "", story.source, story.source_ref)
    folder = videos.video_dir(tmp_path / "out", vid)
    (folder / "script.json").write_text(script.to_json(s), encoding="utf-8")
    store_videos.record_stage(conn, vid, StageRun("script", "script.json", 0, 1, NOW))
    png = tmp_path / "p.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    assert main(["visuals", vid, "--add", "1", str(png)], env) == 0
    out = capsys.readouterr().out
    assert "picture 2 is missing:" in out
    assert "A vine." in out
    assert "picture 1" not in out
    assert main(["visuals", vid, "--add", "two", str(png)], env) == 3
    assert "not a number" in capsys.readouterr().err
    assert main(["visuals", vid, "--add", "2", str(png)], env) == 0
    assert capsys.readouterr().out.strip().endswith("all pictures in")


def test_render_lists_the_missing_stages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_OUT_DIR": str(tmp_path / "out")}
    assert main(["video", "new", "--framing", "fable", "--length", "short"], env) == 0
    vid = capsys.readouterr().out.strip()
    assert main(["render", vid], env) == 3
    assert "still needs: voice, captions, visuals" in capsys.readouterr().err


def test_make_waits_for_the_pick(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_OUT_DIR": str(tmp_path / "out")}
    assert main(["video", "new", "--framing", "fable", "--length", "short"], env) == 0
    vid = capsys.readouterr().out.strip()
    assert main(["make", vid], env) == 0
    out = capsys.readouterr().out
    assert "total\t$0.000\t0s" in out
    assert f"waiting for a story pick: reel-bot video choose {vid}" in out


def test_a_bad_spend_cap_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"REEL_BOT_DB_PATH": str(tmp_path / "db"), "REEL_BOT_MONTHLY_CAP_USD": "lots"}
    assert main(["render", "vid_01ARZ3NDEKTSV4RRFFQ69G5FAV"], env) == 2
    assert "REEL_BOT_MONTHLY_CAP_USD" in capsys.readouterr().err


def test_daily_needs_the_narrator_and_retries_only_failed_videos(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {
        "REEL_BOT_DB_PATH": str(tmp_path / "reel-bot.db"),
        "RELAY_URL": "https://relay.example.test",
        "RELAY_API_KEY": "rk_test_not_a_real_key",
    }
    assert main(["daily", "tick"], env) == 2
    assert "REEL_BOT_NARRATOR" in capsys.readouterr().err
    assert main(["daily", "tick"], {**env, "REEL_BOT_NARRATOR": "tara"}) == 0
    assert capsys.readouterr().out == ""
    assert main(["daily", "retry", "vid_X"], env) == 3
    assert "has not failed" in capsys.readouterr().err
