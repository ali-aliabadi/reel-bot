import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from reel_bot import guide

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed; the guide is encoded with them",
)
TEXT = "One two three four five six.\n\nSeven eight nine ten.\n\n\n  \n"


def silence(seconds: float) -> guide.Speak:
    """A Speak that writes `seconds` of silence and records what it was asked to read."""

    def speak(text: str, out: Path) -> None:
        speak.calls.append(text)  # type: ignore[attr-defined]
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * int(seconds * 24000))

    speak.calls = []  # type: ignore[attr-defined]
    return speak


def test_paragraphs() -> None:
    assert guide.paragraphs(TEXT) == ["One two three four five six.", "Seven eight nine ten."]


def test_tempo_makes_the_reading_last_words_over_pace() -> None:
    assert guide.tempo(10 / guide.PACE, 10) == pytest.approx(1.0)
    assert guide.tempo(5 / guide.PACE, 10) == pytest.approx(0.5)
    assert guide.tempo(100.0, 10) == 2.0  # atempo's range
    assert guide.tempo(0.001, 10) == 0.5
    for seconds, words in ((0.0, 10), (5.0, 0), (-1.0, 3)):
        with pytest.raises(guide.GuideError, match="no audio"):
            guide.tempo(seconds, words)


@needs_ffmpeg
def test_make_reads_at_the_planned_pace_and_reuses_it(tmp_path: Path) -> None:
    speak = silence(3.0)
    path = guide.make(tmp_path, TEXT, speak)
    assert path == tmp_path / guide.FILENAME
    probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
    done = subprocess.run([*probe, path], capture_output=True, text=True, check=True)  # noqa: S603
    seconds = done.stdout
    assert float(seconds) == pytest.approx(10 / guide.PACE, abs=0.15)
    assert sorted(p.name for p in tmp_path.iterdir()) == [guide.FILENAME]
    assert guide.make(tmp_path, TEXT, speak) == path
    assert speak.calls == [TEXT]  # type: ignore[attr-defined]


@needs_ffmpeg
def test_make_refuses_audio_it_cannot_read(tmp_path: Path) -> None:
    def garbage(text: str, out: Path) -> None:
        out.write_bytes(b"not a wav")

    with pytest.raises(guide.GuideError, match="can't be read"):
        guide.make(tmp_path, TEXT, garbage)
    with pytest.raises(guide.GuideError, match="no audio"):
        guide.make(tmp_path, TEXT, silence(0))
    assert not (tmp_path / guide.FILENAME).exists()


def test_make_needs_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def nowhere(name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", nowhere)
    with pytest.raises(guide.GuideError, match="ffmpeg"):
        guide.make(tmp_path, TEXT, silence(1))


def test_local_needs_the_model(tmp_path: Path) -> None:
    (tmp_path / guide.MODEL_FILE).write_bytes(b"")
    with pytest.raises(guide.GuideError, match="make models"):
        guide.local(tmp_path)


def test_local_reports_a_failed_kokoro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (guide.MODEL_FILE, guide.VOICES_FILE):
        (tmp_path / name).write_bytes(b"")
    fail = "import sys; sys.stdin.read(); sys.stderr.write('boom'); sys.exit(3)"
    monkeypatch.setattr(guide, "KOKORO", [sys.executable, "-c", fail])
    with pytest.raises(guide.GuideError, match="Kokoro exited 3: boom"):
        guide.local(tmp_path)("Hello.", tmp_path / "out.wav")
