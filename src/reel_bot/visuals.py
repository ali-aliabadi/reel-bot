"""Visuals stage, pure core: one picture prompt per paragraph.

Each paragraph of the script carries a one-sentence description of its scene;
this module adds the channel's fixed style and the frame's needs (square,
quiet ground for captions) and names the files. The hook is shown over the
first picture and the lesson over the last, so only paragraphs get pictures.
Tara makes the pictures from these prompts; `visuals_write` saves them.
"""

from dataclasses import dataclass

from reel_bot.image_style import FRAME, PICTURE_STYLE
from reel_bot.script import Script

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_SUFFIX = ".gif"
SUFFIXES = (".png", ".jpg", ".webp")  # still image providers
SCENE_SUFFIXES = (*SUFFIXES, GIF_SUFFIX)
GIF_MAX_PIXELS = 4096 * 4096


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


def scene_suffix(data: bytes) -> str:
    """Identify a scene asset by its bytes, including GIF87a and GIF89a."""
    if data.startswith((b"GIF87a", b"GIF89a")):
        return GIF_SUFFIX
    try:
        return suffix(data)
    except VisualsError as err:
        raise VisualsError("the picture is not a PNG, JPEG, WebP or GIF") from err
