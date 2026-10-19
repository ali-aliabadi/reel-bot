import random

import pytest

from reel_bot.stories import CatalogError, Story, candidates, load_builtin, parse


def entry(**overrides: str) -> dict[str, str]:
    base = {
        "id": "aesop-fox-and-grapes",
        "tale": "fox-and-grapes",
        "title": "The Fox and the Grapes",
        "collection": "Aesop",
        "source": "Townsend (1867)",
        "source_ref": "The Fox and the Grapes",
    }
    return base | overrides


def story(story_id: str, tale: str) -> Story:
    return Story(story_id, tale, "Title", "Collection", "Source", "Ref")


def test_parse_valid_entry() -> None:
    assert parse([entry()]) == [
        Story(
            "aesop-fox-and-grapes",
            "fox-and-grapes",
            "The Fox and the Grapes",
            "Aesop",
            "Townsend (1867)",
            "The Fox and the Grapes",
        )
    ]


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ({}, "must be a list"),
        (["x"], "must be an object"),
        ([{"id": "a"}], "needs exactly the fields"),
        ([entry() | {"extra": "x"}], "needs exactly the fields"),
        ([entry(title=" ")], "non-empty string"),
        ([entry(id="Fox Grapes")], "id must be lowercase"),
        ([entry(tale="fox_grapes")], "tale must be lowercase"),
        ([entry(), entry()], "duplicate id"),
    ],
)
def test_parse_rejects(raw: object, problem: str) -> None:
    with pytest.raises(CatalogError, match=problem):
        parse(raw)


def test_parse_collects_every_problem() -> None:
    with pytest.raises(CatalogError) as err:
        parse([entry(id="Bad"), entry(title="")])
    assert len(err.value.problems) == 2


def test_builtin_catalog_is_valid() -> None:
    catalog = load_builtin()
    assert len(catalog) >= 10
    assert len({s.id for s in catalog}) == len(catalog)


def test_candidates_skip_used_tales_and_never_repeat_a_tale() -> None:
    catalog = [
        story("kalilah-lion", "lion-and-hare"),
        story("panchatantra-lion", "lion-and-hare"),
        story("aesop-fox", "fox-and-grapes"),
        story("masnavi-parrot", "merchant-and-parrot"),
    ]
    for seed in range(20):
        got = candidates(catalog, {"merchant-and-parrot"}, 3, random.Random(seed))
        tales = [s.tale for s in got]
        assert sorted(tales) == ["fox-and-grapes", "lion-and-hare"]


def test_candidates_when_everything_is_used() -> None:
    assert candidates([story("a", "t")], {"t"}, 3, random.Random(0)) == []
