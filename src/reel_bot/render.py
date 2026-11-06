"""Render stage, pure core: the edit timeline, the caption file and ffmpeg's arguments.

Pictures change at the pauses between parts: each cut sits halfway through the
silence between one part's last word and the next part's first. The hook plays
over the first picture and the lesson over the last. Each picture zooms gently
and pans across an oversized frame; short dissolves straddle the scene boundaries.
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
CRF, PRESET, THREADS = 20, "veryfast", 2  # two threads leave a core for Relay on the VPS
# x264 looks 10 frames ahead instead of 40: about 200 MB less at 1080x1920 (636 -> 438 MB on
# the VPS), which the tick's 700 MB cap needs; slow pans over still pictures lose nothing.
LOOKAHEAD = 10
DISSOLVE_S = 0.35


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


def _shot_filter(i: int, seconds: float) -> str:
    w, h = round(WIDTH * OVERSCAN / 2) * 2, round(HEIGHT * OVERSCAN / 2) * 2
    progress = f"min(on/{max(1, round(seconds * FPS) - 1)},1)"
    zoom = f"1+0.06*{progress}" if i % 2 == 0 else f"1.06-0.06*{progress}"
    y = f"(ih-ih/zoom)*{progress}"
    return (
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"zoompan=z='{zoom}':x='(iw-iw/zoom)/2':y='{y}':"
        f"d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"trim=duration={seconds:.6f},settb=AVTB,setpts=PTS-STARTPTS,"
        f"setsar=1,format=yuv420p[v{i}]"
    )


def ffmpeg_args(shots: Sequence[Shot], voice: str, captions_file: str, out: str) -> list[str]:
    """Motion and overlapping dissolves without shortening the narration timeline."""
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-filter_complex_threads", "1", "-progress", "render.progress.txt",
            "-stats_period", "2"]  # fmt: skip
    # Half a dissolve on either side keeps each caption-aligned cut at its midpoint.
    fade = min(DISSOLVE_S, min(s.seconds for s in shots) / 2)
    durations = [
        s.seconds + (fade / 2 if i else 0) + (fade / 2 if i < len(shots) - 1 else 0)
        for i, s in enumerate(shots)
    ]
    for s, seconds in zip(shots, durations, strict=True):
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.6f}",
                 "-threads", "1", "-i", s.picture]  # fmt: skip
    args += ["-i", voice]
    graph = [_shot_filter(i, seconds) for i, seconds in enumerate(durations)]
    previous = "v0"
    elapsed = durations[0]
    for i in range(1, len(shots)):
        graph.append(
            f"[{previous}][v{i}]xfade=transition=fade:duration={fade:.6f}:"
            f"offset={elapsed - fade:.6f}[mix{i}]"
        )
        previous = f"mix{i}"
        elapsed += durations[i] - fade
    graph.append(f"[{previous}]ass={captions_file},format=yuv420p[out]")
    args += ["-filter_complex", ";".join(graph), "-map", "[out]", "-map", f"{len(shots)}:a"]
    args += ["-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-threads", str(THREADS)]
    args += ["-x264-params", f"rc-lookahead={LOOKAHEAD}"]
    args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out]
    return args


def pictures_from(visuals_json: str) -> list[tuple[str, str]]:
    data = json.loads(visuals_json)
    return [(str(p["section"]), Path(str(p["path"])).name) for p in data["pictures"]]
