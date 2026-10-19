"""Visuals stage, pure core: one picture prompt per paragraph.

Each paragraph of the script carries a one-sentence description of its scene;
this module adds the channel's fixed style and the frame's needs (vertical,
calm lower third for captions) and names the files. The hook is shown over the
first picture and the lesson over the last, so only paragraphs get pictures.
The API call lives in `visuals_write`.
"""

from dataclasses import dataclass

from reel_bot.script import PICTURE_STYLE, Script

FRAME = (
    "Vertical 2:3 composition for a phone screen; the main figures in the upper "
    "two thirds; the lower third quiet (ground, water or border) so captions stay "
    "readable."
)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class VisualsError(Exception):
    pass


@dataclass(frozen=True)
class Picture:
    section: str  # "p1".."pN", matching the captions' sections
    path: str  # relative to the video's directory
    prompt: str


@dataclass(frozen=True)
class Visuals:
    video_id: str
    pictures: tuple[Picture, ...]


def prompt(script: Script, scene: str) -> str:
    return (
        f"An illustration for the fable {script.title!r}. Scene: {scene}\n"
        f"Style: {PICTURE_STYLE}.\n{FRAME}"
    )


def plan(script: Script) -> Visuals:
    """The pictures this script needs, in order."""
    if not script.paragraphs:
        raise VisualsError("the script has no paragraphs to draw")
    return Visuals(
        script.video_id,
        tuple(
            Picture(f"p{i}", f"picture-{i}.png", prompt(script, p.picture))
            for i, p in enumerate(script.paragraphs, 1)
        ),
    )


def check_png(data: bytes) -> None:
    if not data.startswith(PNG_MAGIC):
        raise VisualsError("the picture is not a PNG")
