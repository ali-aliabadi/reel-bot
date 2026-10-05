"""Command-line entry point. Subcommands are added here as roadmap items land."""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

from reel_bot import __version__, config

EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reel-bot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version")
    sub.add_parser("config", help="check the configuration and show it without secrets")
    return parser


def _show_config(env: Mapping[str, str]) -> int:
    try:
        cfg = config.load(env)
    except config.ConfigError as err:
        for problem in err.problems:
            print(f"config: {problem}", file=sys.stderr)
        return EXIT_CONFIG
    for name, value in cfg.describe().items():
        print(f"{name}={value}")
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "config":
        return _show_config(os.environ if env is None else env)
    print(__version__)
    return 0
