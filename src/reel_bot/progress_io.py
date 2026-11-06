"""Small, best-effort numeric checkpoints shared with offline child processes."""

import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)
STAGES = ("voice", "captions", "render")


@dataclass(frozen=True)
class Checkpoint:
    done: float
    total: float
    started: float
    updated: float


def read(folder: Path, stage: str) -> Checkpoint | None:
    try:
        data = json.loads((folder / f"{stage}.progress.json").read_text())
        values = [float(data[k]) for k in ("done", "total", "started", "updated")]
        if not all(math.isfinite(v) and v >= 0 for v in values) or values[1] <= 0:
            return None
        return Checkpoint(*values)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


def write(folder: Path, stage: str, done: float, total: float) -> None:
    if stage not in STAGES:
        raise ValueError("unknown progress stage")
    now = time.time()
    old = read(folder, stage) if done else None
    progress = Checkpoint(done, total, old.started if old else now, now)
    path = folder / f"{stage}.progress.json"
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(progress)))
        tmp.replace(path)
    except OSError:
        log.warning("progress checkpoint could not be written")


def rendered_seconds(folder: Path) -> float:
    """ffmpeg's latest completed output time, bounded against a broken progress file."""
    try:
        with (folder / "render.progress.txt").open("rb") as file:
            file.seek(0, 2)
            file.seek(max(0, file.tell() - 32768))
            lines = file.read().decode(errors="replace").splitlines()
        values = [int(line.split("=", 1)[1]) for line in lines if line.startswith("out_time_us=")]
        return max(0.0, values[-1] / 1_000_000) if values else 0.0
    except (OSError, ValueError):
        return 0.0
