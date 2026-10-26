"""Kokoro in its own process: `python -m reel_bot.kokoro MODEL_DIR OUT_WAV` reads
the script on stdin and writes it read aloud, a breath between paragraphs.

`guide.local` runs it as a child process, like Whisper, so the model's memory is
given back afterwards and a run that hangs can be timed out.
"""

# A thin edge over untyped libraries (onnxruntime, kokoro-onnx, espeakng-loader).
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

import sys
import wave
from pathlib import Path

import espeakng_loader
import numpy as np
import onnxruntime as ort
from kokoro_onnx import Kokoro
from kokoro_onnx.config import EspeakConfig

from reel_bot import guide


def main(argv: list[str]) -> None:
    model_dir, out = Path(argv[0]), argv[1]
    opts = ort.SessionOptions()
    opts.intra_op_num_threads, opts.inter_op_num_threads = guide.THREADS, 1
    session = ort.InferenceSession(str(model_dir / guide.MODEL_FILE), opts)
    espeak = EspeakConfig(espeakng_loader.get_library_path(), espeakng_loader.get_data_path())
    kokoro = Kokoro.from_session(session, str(model_dir / guide.VOICES_FILE), espeak_config=espeak)
    parts: list[np.ndarray] = []  # pyright: ignore[reportMissingTypeArgument]
    rate = 24000
    for paragraph in guide.paragraphs(sys.stdin.read()):
        audio, rate = kokoro.create(paragraph, voice=guide.VOICE, speed=guide.SPEED, lang="en-us")
        parts += [audio, np.zeros(int(guide.GAP_S * rate), dtype=np.float32)]
    samples = np.concatenate(parts[:-1]) if parts else np.zeros(1, dtype=np.float32)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


if __name__ == "__main__":
    main(sys.argv[1:])
