import dataclasses

import pytest

from reel_bot import visuals
from reel_bot.script import PICTURE_STYLE, Paragraph, Script

SCRIPT = Script(
    video_id="vid_X",
    story_id="aesop-fox",
    title="The Fox and the Grapes",
    hook="A fox wanted grapes.",
    paragraphs=(
        Paragraph("He jumped and missed.", "A fox leaping at a vine."),
        Paragraph("He walked away, proud.", "A fox walking off at dusk."),
    ),
    lesson="",
    source="Townsend",
    source_ref="p. 1",
)


def test_plan_draws_one_picture_per_paragraph() -> None:
    plan = visuals.plan(SCRIPT)
    assert [(p.section, p.path) for p in plan.pictures] == [
        ("p1", "picture-1.png"),
        ("p2", "picture-2.png"),
    ]
    first = plan.pictures[0].prompt
    assert "A fox leaping at a vine." in first
    assert PICTURE_STYLE in first
    assert "lower third" in first
    assert "'The Fox and the Grapes'" in first


def test_plan_needs_paragraphs() -> None:
    with pytest.raises(visuals.VisualsError):
        visuals.plan(dataclasses.replace(SCRIPT, paragraphs=()))


def test_check_png() -> None:
    visuals.check_png(visuals.PNG_MAGIC + b"rest")
    with pytest.raises(visuals.VisualsError, match="not a PNG"):
        visuals.check_png(b"<html>")
