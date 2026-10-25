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
    assert [(p.section, p.name) for p in plan.pictures] == [
        ("p1", "picture-1"),
        ("p2", "picture-2"),
    ]
    first = plan.pictures[0].prompt
    assert "A fox leaping at a vine." in first
    assert PICTURE_STYLE in first
    assert "lower third" in first
    assert "'The Fox and the Grapes'" in first


def test_plan_needs_paragraphs() -> None:
    with pytest.raises(visuals.VisualsError):
        visuals.plan(dataclasses.replace(SCRIPT, paragraphs=()))


def test_suffix_comes_from_the_bytes() -> None:
    assert visuals.suffix(visuals.PNG_MAGIC + b"rest") == ".png"
    assert visuals.suffix(b"\xff\xd8\xff\xe0rest") == ".jpg"
    assert visuals.suffix(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    for odd in (b"<html>", b"GIF89a", b"", b"RIFF\x00\x00\x00\x00WAVE"):
        with pytest.raises(visuals.VisualsError, match="not a PNG, JPEG or WebP"):
            visuals.suffix(odd)
