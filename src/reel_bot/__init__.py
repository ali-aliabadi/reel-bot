"""reel-bot: writes, renders and posts short-form videos, with one human pick per video."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("reel-bot")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "dev"
