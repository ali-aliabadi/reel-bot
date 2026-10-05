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
