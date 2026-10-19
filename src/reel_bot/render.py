"""Render stage, pure core: the edit timeline, the caption file and ffmpeg's arguments.

Pictures change at the pauses between parts: each cut sits halfway through the
silence between one part's last word and the next part's first. The hook plays
over the first picture and the lesson over the last. Each picture pans slowly
(alternately down and up) across a frame scaled a little larger than 1080x1920.
Captions are an ASS file burned in by libass. `render_write` runs ffmpeg.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from reel_bot.captions import Captions, Cue

WIDTH, HEIGHT, FPS = 1080, 1920, 30
OVERSCAN = 1.1  # the picture is scaled this much larger than the frame to leave room to pan
FONT = "DejaVu Sans"  # Ubuntu's fonts-dejavu-core; free licence, commercial use allowed
FONT_SIZE = 84
CAPTION_MARGIN = 420  # px from the bottom, above the Shorts buttons and title
CRF, PRESET, THREADS = 20, "medium", 2  # two threads leave a core for Relay on the VPS


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class Shot:
    picture: str  # file name in the video's directory
    start: float
    seconds: float


def timeline(c: Captions, pictures: Sequence[tuple[str, str]], total: float) -> tuple[Shot, ...]:
    """One shot per picture. `pictures` is (section, file) in order; `total` is the
    take's length in seconds."""
    starts = {s.name: s for s in c.sections}
    ends = {s.name: s.end for s in c.sections}
    order = [s.name for s in c.sections]
    cuts = [0.0]
    for section, _ in pictures[1:]:
        if section not in starts:
            raise RenderError(f"no spoken part {section!r} for its picture")
        before = order[order.index(section) - 1]
        cuts.append((ends[before] + starts[section].start) / 2)
    cuts.append(total)
    shots = tuple(
        Shot(file, round(cuts[i], 3), round(cuts[i + 1] - cuts[i], 3))
        for i, (_, file) in enumerate(pictures)
    )
    if any(s.seconds <= 0 for s in shots):
        raise RenderError("the pictures' times overlap; the captions do not fit this take")
    return shots


def _ass_time(t: float) -> str:
    cs = round(max(t, 0.0) * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass(cues: Sequence[Cue]) -> str:
    """The caption track as an ASS file: white bold text with a black outline."""
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\nPlayResY: {HEIGHT}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{FONT},{FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,6,2,2,60,60,{CAPTION_MARGIN},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [
        f"Dialogue: 0,{_ass_time(q.start)},{_ass_time(q.end)},Caption,,0,0,0,,{_ass_text(q.text)}"
        for q in cues
    ]
    return head + "\n".join(lines) + "\n"


def _shot_filter(i: int, shot: Shot) -> str:
    w, h = round(WIDTH * OVERSCAN / 2) * 2, round(HEIGHT * OVERSCAN / 2) * 2
    travel = f"(ih-{HEIGHT})*min(t/{shot.seconds},1)"
    y = travel if i % 2 == 0 else f"(ih-{HEIGHT})-{travel}"
    return (
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT}:(iw-{WIDTH})/2:'{y}',setsar=1,fps={FPS},format=yuv420p[v{i}]"
    )


def ffmpeg_args(shots: Sequence[Shot], voice: str, captions_file: str, out: str) -> list[str]:
    """ffmpeg arguments, run with the video's directory as the working directory so
    every file name is relative (and needs no filter escaping)."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for s in shots:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{s.seconds:.3f}", "-i", s.picture]
    args += ["-i", voice]
    graph = [_shot_filter(i, s) for i, s in enumerate(shots)]
    joined = "".join(f"[v{i}]" for i in range(len(shots)))
    graph.append(f"{joined}concat=n={len(shots)}:v=1:a=0,ass={captions_file}[out]")
    args += ["-filter_complex", ";".join(graph), "-map", "[out]", "-map", f"{len(shots)}:a"]
    args += ["-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-threads", str(THREADS)]
    args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out]
    return args


def pictures_from(visuals_json: str) -> list[tuple[str, str]]:
    data = json.loads(visuals_json)
    return [(str(p["section"]), Path(str(p["path"])).name) for p in data["pictures"]]
