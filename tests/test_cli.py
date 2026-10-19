from datetime import UTC, datetime
from pathlib import Path

import pytest

from reel_bot import __version__
from reel_bot.cli import main


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
