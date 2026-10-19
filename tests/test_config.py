from pathlib import Path

import pytest

from reel_bot import config

KEY = "rk_test_not_a_real_key"
VALID = {"RELAY_URL": "https://relay.example.test/", "RELAY_API_KEY": KEY}


def test_defaults_apply_when_optional_values_are_unset() -> None:
    cfg = config.load(VALID)
    assert cfg.relay_url == "https://relay.example.test"
    assert cfg.relay_app == "reel-bot"
    assert cfg.relay_user == "admin"
    assert cfg.db_path == Path("./data/reel-bot.db")
    assert cfg.out_dir == Path("./out")


def test_explicit_values_override_defaults() -> None:
    cfg = config.load(
        {
            **VALID,
            "RELAY_APP": "reel-bot-dev",
            "RELAY_USER": "ali",
            "REEL_BOT_DB_PATH": "/srv/reel-bot/x.db",
            "REEL_BOT_OUT_DIR": "/srv/reel-bot/out",
        }
    )
    assert (cfg.relay_app, cfg.relay_user) == ("reel-bot-dev", "ali")
    assert (cfg.db_path, cfg.out_dir) == (Path("/srv/reel-bot/x.db"), Path("/srv/reel-bot/out"))


@pytest.mark.parametrize(
    ("env", "problems"),
    [
        ({}, ["RELAY_URL is required", "RELAY_API_KEY is required"]),
        ({"RELAY_URL": "  ", "RELAY_API_KEY": " "}, ["RELAY_URL is required", "RELAY_API_KEY"]),
        ({**VALID, "RELAY_URL": "http://relay.example.test"}, ["must be an https URL"]),
        ({**VALID, "RELAY_URL": "relay.example.test"}, ["must be an https URL"]),
    ],
)
def test_every_problem_is_reported(env: dict[str, str], problems: list[str]) -> None:
    with pytest.raises(config.ConfigError) as exc:
        config.load(env)
    assert len(exc.value.problems) == len(problems)
    for got, want in zip(exc.value.problems, problems, strict=True):
        assert want in got


def test_api_key_never_appears_in_repr_or_describe() -> None:
    cfg = config.load(VALID)
    assert KEY not in repr(cfg)
    assert KEY not in str(cfg.describe())
    assert cfg.describe()["RELAY_API_KEY"] == "set"


def test_error_message_never_contains_values() -> None:
    with pytest.raises(config.ConfigError) as exc:
        config.load({"RELAY_URL": "http://secret-host.test", "RELAY_API_KEY": KEY})
    assert "secret-host" not in str(exc.value)
    assert KEY not in str(exc.value)
