import pytest

from reel_bot import render
from reel_bot.captions import Captions, Cue, Section
from reel_bot.render import Shot

CAP = Captions(
    video_id="vid_X",
    words=(),
    cues=(Cue("A fox {wanted}", 0.0, 1.25), Cue("grapes", 61.5, 3723.456)),
    sections=(
        Section("hook", 0.2, 2.0),
        Section("p1", 2.6, 10.0),
        Section("p2", 11.0, 20.0),
        Section("lesson", 20.4, 24.0),
    ),
    matched=1.0,
)


def test_timeline_cuts_in_the_pauses_and_runs_to_the_end_of_the_take() -> None:
    shots = render.timeline(CAP, [("p1", "picture-1.png"), ("p2", "picture-2.png")], 25.0)
    assert shots == (Shot("picture-1.png", 0.0, 10.5), Shot("picture-2.png", 10.5, 14.5))


def test_timeline_refuses_a_picture_without_its_part_or_a_short_take() -> None:
    with pytest.raises(render.RenderError, match="p9"):
        render.timeline(CAP, [("p1", "a.png"), ("p9", "b.png")], 25.0)
    with pytest.raises(render.RenderError, match="overlap"):
        render.timeline(CAP, [("p1", "a.png"), ("p2", "b.png")], 9.0)


def test_ass_has_the_frame_style_and_escaped_cues() -> None:
    out = render.ass(CAP.cues)
    assert "PlayResX: 1080\nPlayResY: 1920" in out
    assert f"Style: Caption,{render.FONT},{render.FONT_SIZE}," in out
    assert "Dialogue: 0,0:00:00.00,0:00:01.25,Caption,,0,0,0,,A fox (wanted)" in out
    assert "Dialogue: 0,0:01:01.50,1:02:03.46,Caption,,0,0,0,,grapes" in out


def test_ffmpeg_args_keep_timing_with_motion_and_dissolves() -> None:
    shots = (Shot("picture-1.png", 0.0, 10.5), Shot("picture-2.png", 10.5, 14.5))
    args = render.ffmpeg_args(shots, "voice.wav", "captions.ass", "out.mp4")
    assert args[0] == "ffmpeg"
    assert args[-1] == "out.mp4"
    assert args.count("-loop") == 2
    assert args[args.index("-threads", args.index("-c:v")) + 1] == "2"
    assert args[args.index("-x264-params") + 1] == "rc-lookahead=10"  # the memory cap needs it
    graph = args[args.index("-filter_complex") + 1]
    assert "crop=1188:2112,zoompan" in graph
    assert "zoompan=z='1+0.06*" in graph
    assert "zoompan=z='1.06-0.06*" in graph
    assert "xfade=transition=fade:duration=0.350000:offset=10.325000" in graph
    assert "ass=captions.ass,format=yuv420p[out]" in graph
    assert args[args.index("-filter_complex_threads") + 1] == "1"
    assert args[args.index("-map", args.index("-map") + 1) + 1] == "2:a"


def test_pictures_from_keeps_only_file_names() -> None:
    raw = '{"pictures": [{"section": "p1", "path": "../x/picture-1.png", "prompt": "p"}]}'
    assert render.pictures_from(raw) == [("p1", "picture-1.png")]


def test_single_picture_and_short_shots() -> None:
    one = render.ffmpeg_args([Shot("a.png", 0, 5)], "v.wav", "c.ass", "out.mp4")
    graph = one[one.index("-filter_complex") + 1]
    assert "xfade" not in graph
    assert "[v0]ass=" in graph
    short = render.ffmpeg_args(
        [Shot("a.png", 0, 0.2), Shot("b.png", 0.2, 0.3)], "v.wav", "c.ass", "out.mp4"
    )
    assert "duration=0.100000:offset=0.150000" in short[short.index("-filter_complex") + 1]
