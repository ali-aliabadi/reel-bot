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
MP4_SUFFIX = ".mp4"
SUFFIXES = (".png", ".jpg", ".webp")  # still image providers
SCENE_SUFFIXES = (*SUFFIXES, GIF_SUFFIX, MP4_SUFFIX)
ANIMATION_MAX_PIXELS = 4096 * 4096
ANIMATION_INPUTS = {
    GIF_SUFFIX: ("-f", "gif", "-ignore_loop", "1"),
    MP4_SUFFIX: ("-f", "mov", "-enable_drefs", "0", "-protocol_whitelist", "file"),
}
_MIN_FILE_TYPE_BOX = 16  # size, type, major brand and minor version


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
    """Identify stills, GIFs and MP4 clips by their bytes."""
    if data.startswith((b"GIF87a", b"GIF89a")):
        return GIF_SUFFIX
    if data[4:8] == b"ftyp" and _MIN_FILE_TYPE_BOX <= int.from_bytes(data[:4], "big") <= len(data):
        return MP4_SUFFIX
    try:
        return suffix(data)
    except VisualsError as err:
        raise VisualsError("the scene needs a PNG, JPEG, WebP, GIF or MP4") from err
