"""Whisper in its own process: `python -m reel_bot.whisper MODEL_DIR AUDIO` prints
what it heard as JSON: {"duration": seconds, "words": [[word, start, end], ...]}.

The captions stage runs it as a child process (`captions_write.local`) so the
model's memory is all given back before the render starts, and a run that hangs
can be timed out. It gets no prompt: Whisper skips words it was prompted with.
"""

import json
import sys
from pathlib import Path

# It ships inline types but no py.typed marker.
from faster_whisper import WhisperModel  # pyright: ignore[reportMissingTypeStubs]

from reel_bot import progress_io

THREADS = 2  # like ffmpeg, leave the VPS's other services room


def main(argv: list[str]) -> None:
    model_dir, audio = argv
    progress_io.write(Path(audio).parent, "captions", 0, 1)
    model = WhisperModel(
        model_dir, device="cpu", compute_type="int8", cpu_threads=THREADS, local_files_only=True
    )
    segments, info = model.transcribe(audio, language="en", word_timestamps=True)  # pyright: ignore[reportUnknownMemberType]
    words: list[list[str | float]] = []
    for seg in segments:
        words.extend([[w.word, w.start, w.end] for w in seg.words or ()])
        progress_io.write(Path(audio).parent, "captions", seg.end, max(1, info.duration))
    sys.stdout.write(json.dumps({"duration": info.duration, "words": words}))


if __name__ == "__main__":
    main(sys.argv[1:])
