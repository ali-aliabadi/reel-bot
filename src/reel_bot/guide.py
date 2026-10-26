"""The reading guide: the script read aloud by Kokoro at the pace the video is
planned for, sent with the voice-note question so Tara can hear it first.

It only helps Tara read; it is never in the video. Kokoro runs on this machine,
free and offline, as `reel_bot.kokoro` in a child process at low priority,
reading the model from `MODEL_DIR` (`make models` fills it once). `make` is
idempotent: a guide on disk is reused.
"""

import shutil
import subprocess
import sys
import wave
from collections.abc import Callable
from pathlib import Path

MODEL_DIR = Path("models") / "kokoro"  # relative to the checkout; see `make models`
MODEL_FILE, VOICES_FILE = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"
KOKORO = [sys.executable, "-m", "reel_bot.kokoro"]  # + model dir and output wav
VOICE = "af_heart"
PACE = 2.3  # words a second; the script's word budgets assume this pace
# ponytail: calibration knob. Kokoro's own pace at speed 1 is about 3.0 words a
# second (measured on the VPS), so it is slowed here and ffmpeg's atempo only
# trims the length to exact.
SPEED = PACE / 3.0
GAP_S = 0.7  # a breath between paragraphs
THREADS = 2  # like ffmpeg and Whisper, leave the VPS's other services room
TIMEOUT_S = 1200  # a 100-word script takes about 4 minutes on the VPS (about 600 MB at peak)
FILENAME = "guide.mp3"

# (script text, output wav) -> None; writes the reading
Speak = Callable[[str, Path], None]


class GuideError(Exception):
    pass


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def tempo(seconds: float, words: int) -> float:
    """ffmpeg's atempo factor that makes `seconds` of reading last words / PACE."""
    if seconds <= 0 or words <= 0:
        raise GuideError("Kokoro made no audio")
    return min(2.0, max(0.5, seconds / (words / PACE)))  # atempo's range


def _run(args: list[str], timeout_s: float, what: str, stdin: bytes = b"") -> None:
    if shutil.which("nice"):
        args = ["nice", "-n", "10", *args]
    try:
        subprocess.run(args, input=stdin, capture_output=True, timeout=timeout_s, check=True)  # noqa: S603
    except subprocess.CalledProcessError as err:
        last = err.stderr.decode(errors="replace").strip().splitlines()[-1:]
        raise GuideError(f"{what} exited {err.returncode}: {''.join(last)}") from err
    except subprocess.TimeoutExpired as err:
        raise GuideError(f"{what} took over {timeout_s:.0f} s") from err


def local(model_dir: Path = MODEL_DIR, timeout_s: float = TIMEOUT_S) -> Speak:
    """A Speak running Kokoro on this machine, in a child process."""
    if not all((model_dir / f).is_file() for f in (MODEL_FILE, VOICES_FILE)):
        raise GuideError(f"no Kokoro model in {model_dir}: run make models")

    def speak(text: str, out: Path) -> None:
        _run([*KOKORO, str(model_dir), str(out)], timeout_s, "Kokoro", text.encode())

    return speak


def make(folder: Path, text: str, speak: Speak) -> Path:
    """The guide for this script, reused when it is already on disk."""
    path = folder / FILENAME
    if path.is_file():
        return path
    if shutil.which("ffmpeg") is None:
        raise GuideError("ffmpeg is not installed")
    raw, tmp = folder / "guide.wav", folder / "guide.tmp.mp3"
    speak(text, raw)
    try:
        with wave.open(str(raw), "rb") as w:
            seconds = w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error) as err:
        raise GuideError("Kokoro's audio can't be read") from err
    factor = tempo(seconds, len(text.split()))
    args = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(raw),
            "-filter:a", f"atempo={factor:.4f}", "-ac", "1", "-b:a", "64k", str(tmp)]  # fmt: skip
    _run(args, 120, "ffmpeg")
    tmp.replace(path)
    raw.unlink()
    return path
