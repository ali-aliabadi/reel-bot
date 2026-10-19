"""The story catalog: every fable the channel can tell, and which tale each one is.

A *tale* is the story itself; a *story* is one version of it in one collection.
The lion and the hare is one tale told in both Kalilah and Dimnah and the
Panchatantra, so once either version is used, both are spent. The rule that no
tale is told twice lives in `store` (a primary key on the tale); this module
holds the data and the pure selection logic.
"""

import json
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from typing import cast

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FIELDS = ("id", "tale", "title", "collection", "source", "source_ref")


class CatalogError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid story catalog: " + "; ".join(problems))


@dataclass(frozen=True)
class Story:
    id: str  # unique per version, e.g. "kalilah-lion-and-hare"
    tale: str  # shared by every version of the same story, e.g. "lion-and-hare"
    title: str
    collection: str  # e.g. "Kalilah and Dimnah"
    source: str  # the public-domain translation it is retold from
    source_ref: str  # where in that source, e.g. "Book 1"


def _story(item: object, where: str) -> Story | str:
    """One catalog entry as a Story, or a problem description."""
    if not isinstance(item, dict):
        return f"{where}: must be an object"
    entry = cast(dict[str, object], item)
    if set(entry) != set(FIELDS):
        return f"{where}: needs exactly the fields {', '.join(FIELDS)}"
    values = [entry[key] for key in FIELDS]
    if not all(isinstance(v, str) and v.strip() for v in values):
        return f"{where}: every field must be a non-empty string"
    story = Story(*cast(list[str], values))
    for key in ("id", "tale"):
        if not SLUG.match(getattr(story, key)):
            return f"{where}: {key} must be lowercase words joined by '-'"
    return story


def parse(raw: object) -> list[Story]:
    """Validate a decoded catalog (a JSON list of objects) and return its stories."""
    if not isinstance(raw, list):
        raise CatalogError(["catalog must be a list of stories"])
    problems: list[str] = []
    stories: list[Story] = []
    seen: set[str] = set()
    for n, item in enumerate(cast(list[object], raw), start=1):
        result = _story(item, f"story {n}")
        if isinstance(result, str):
            problems.append(result)
            continue
        if result.id in seen:
            problems.append(f"story {n}: duplicate id {result.id!r}")
        seen.add(result.id)
        stories.append(result)
    if problems:
        raise CatalogError(problems)
    return stories


def load_builtin() -> list[Story]:
    """The catalog shipped with the package (src/reel_bot/data/stories.json)."""
    text = resources.files("reel_bot").joinpath("data/stories.json").read_text("utf-8")
    return parse(json.loads(text))


def candidates(
    stories: Iterable[Story], used_tales: set[str], n: int, rng: random.Random
) -> list[Story]:
    """Up to n stories from different, unused tales, for ali to pick from."""
    by_tale: dict[str, list[Story]] = {}
    for story in stories:
        if story.tale not in used_tales:
            by_tale.setdefault(story.tale, []).append(story)
    tales = sorted(by_tale)
    picked = rng.sample(tales, min(n, len(tales)))
    return [rng.choice(by_tale[tale]) for tale in picked]
