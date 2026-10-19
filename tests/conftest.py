from collections.abc import Callable
from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "snapshots"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots", action="store_true", help="rewrite tests/snapshots from the output"
    )


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    """snapshot(name, text) compares text with tests/snapshots/<name>."""
    update = bool(request.config.getoption("--update-snapshots"))

    def check(name: str, text: str) -> None:
        path = SNAPSHOTS / name
        if update or not path.exists():
            path.write_text(text, encoding="utf-8")
        assert text == path.read_text(encoding="utf-8"), (
            f"{name} changed; rerun with --update-snapshots"
        )

    return check
