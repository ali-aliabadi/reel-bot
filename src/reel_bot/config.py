"""Configuration from environment variables (docs/ARCHITECTURE.md, Configuration)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_RELAY_APP = "reel-bot"
DEFAULT_RELAY_USER = "admin"
DEFAULT_DB_PATH = "./data/reel-bot.db"
DEFAULT_OUT_DIR = "./out"


class ConfigError(Exception):
    """One or more settings are missing or invalid. Messages never contain secret values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid configuration: " + "; ".join(problems))


@dataclass(frozen=True)
class Config:
    relay_url: str
    relay_api_key: str = field(repr=False)
    relay_app: str
    relay_user: str
    db_path: Path
    out_dir: Path

    def describe(self) -> dict[str, str]:
        """Settings safe to show a person: the API key is reported as set, never shown."""
        return {
            "RELAY_URL": self.relay_url,
            "RELAY_API_KEY": "set",
            "RELAY_APP": self.relay_app,
            "RELAY_USER": self.relay_user,
            "REEL_BOT_DB_PATH": str(self.db_path),
            "REEL_BOT_OUT_DIR": str(self.out_dir),
        }


def load(env: Mapping[str, str]) -> Config:
    """Build a Config from env, reporting every problem at once."""
    problems: list[str] = []

    def get(name: str, default: str = "") -> str:
        return env.get(name, "").strip() or default

    relay_url = get("RELAY_URL").rstrip("/")
    if not relay_url:
        problems.append("RELAY_URL is required")
    elif urlparse(relay_url).scheme != "https" or not urlparse(relay_url).netloc:
        problems.append("RELAY_URL must be an https URL")

    relay_api_key = get("RELAY_API_KEY")
    if not relay_api_key:
        problems.append(
            "RELAY_API_KEY is required (create one with `relay clients create reel-bot`)"
        )

    if problems:
        raise ConfigError(problems)

    return Config(
        relay_url=relay_url,
        relay_api_key=relay_api_key,
        relay_app=get("RELAY_APP", DEFAULT_RELAY_APP),
        relay_user=get("RELAY_USER", DEFAULT_RELAY_USER),
        db_path=Path(get("REEL_BOT_DB_PATH", DEFAULT_DB_PATH)),
        out_dir=Path(get("REEL_BOT_OUT_DIR", DEFAULT_OUT_DIR)),
    )
