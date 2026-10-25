"""Visuals stage, pure core: one picture prompt per paragraph.

Each paragraph of the script carries a one-sentence description of its scene;
this module adds the channel's fixed style and the frame's needs (vertical,
calm lower third for captions) and names the files. The hook is shown over the
first picture and the lesson over the last, so only paragraphs get pictures.
Tara makes the pictures from these prompts; `visuals_write` saves them.
"""

from dataclasses import dataclass

from reel_bot.script import PICTURE_STYLE, Script

FRAME = (
    "Vertical 2:3 composition for a phone screen; the main figures in the upper "
    "two thirds; the lower third quiet (ground, water or border) so captions stay "
    "readable."
)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SUFFIXES = (".png", ".jpg", ".webp")  # the picture types a video takes


class VisualsError(Exception):
    pass


@dataclass(frozen=True)
class Picture:
    section: str  # "p1".."pN", matching the captions' sections
    name: str  # file name in the video's directory, without the suffix of the picture's type
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
            Picture(f"p{i}", f"picture-{i}", prompt(script, p.picture))
            for i, p in enumerate(script.paragraphs, 1)
        ),
    )


def suffix(data: bytes) -> str:
    """The picture's file suffix, from its first bytes; anything but PNG, JPEG or WebP
    raises VisualsError."""
    if data.startswith(PNG_MAGIC):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise VisualsError("the picture is not a PNG, JPEG or WebP")
