"""Build a concise, honest bot status card from saved work and numeric checkpoints."""

import contextlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from reel_bot import progress_io, script, store_progress, visuals, visuals_write

STALE_S = 180


@dataclass(frozen=True)
class View:
    stage: str
    detail: str = ""
    done: float = 0
    total: float = 0
    started: float | None = None
    updated: float | None = None
    terminal: bool = False


def _processing(folder: Path, stage: str, label: str, s: store_progress.State) -> View:
    p = progress_io.read(folder, stage)
    done = progress_io.rendered_seconds(folder) if stage == "render" else p.done if p else 0
    detail = ""
    if p and stage == "voice":
        detail = f"{int(p.done)} of {int(p.total)} spoken sections generated"
    if p and stage == "captions":
        detail = "Matching the spoken words to captions"
    start = p.started if p else s.running_at.timestamp() if s.running_at else None
    updated = p.updated if p else None
    if stage == "render":
        with contextlib.suppress(OSError):
            updated = (folder / "render.progress.txt").stat().st_mtime
    return View(label, detail, done, p.total if p else 0, start, updated)


def build(s: store_progress.State, folder: Path, worker_active: bool, narration: str) -> View:
    if "sent" in s.steps:
        return View("✅ Complete", "Video sent in this chat", 1, 1, terminal=True)
    if "cancelled" in s.steps:
        return View("Cancelled", terminal=True)
    if "failed" in s.steps:
        return View("⚠️ Stopped", "Needs attention; see the failure alert", terminal=True)
    view = _phase(s, folder, narration)
    if s.errors:
        return View(
            "⚠️ Retrying", f"Attempt {s.errors} failed; next attempt is automatic. {view.stage}"
        )
    if s.running_at and not worker_active:
        return View("⚠️ Interrupted", "Worker stopped; an automatic retry is scheduled")
    return view


def _phase(s: store_progress.State, folder: Path, narration: str) -> View:
    if not s.picked:
        return View("Waiting for your story choice")
    if "script" not in s.stages:
        return View("Waiting for the script", "Reply to the script question with the full answer")
    if "voice" not in s.stages:
        if narration == "tara" and "take" in s.steps:
            return View("Waiting for your voice recording")
        label = "Generating narration" if narration == "kokoro" else "Preparing the voice guide"
        return _processing(folder, "voice", label, s)
    return _visuals_phase(s, folder)


def _visuals_phase(s: store_progress.State, folder: Path) -> View:
    raw = script.from_json((folder / "script.json").read_text())
    pictures = visuals.plan(raw).pictures
    count = sum(visuals_write.found(folder, p) is not None for p in pictures)
    if count < len(pictures):
        return View(
            "Collecting pictures",
            f"{count} of {len(pictures)} received — "
            + (
                "waiting for your image"
                if any(
                    f"picture-{n}" in s.steps and visuals_write.found(folder, p) is None
                    for n, p in enumerate(pictures, 1)
                )
                else "preparing the next picture prompt"
            ),
            count,
            len(pictures),
        )
    if "captions" not in s.stages:
        return _processing(folder, "captions", "Preparing captions", s)
    if "render" not in s.stages:
        return _processing(folder, "render", "Rendering video", s)
    return View("Sending the video", "Render complete; uploading to the bot")


def text(title: str, view: View, now: datetime) -> str:
    lines = [title, "", view.stage]
    if view.total:
        pct = min(
            100 if view.terminal or view.stage == "Collecting pictures" else 99,
            max(0, int(100 * view.done / view.total)),
        )
        filled = pct // 10
        lines.append("█" * filled + "░" * (10 - filled) + f" {pct}%")
    if view.detail:
        lines.append(view.detail)
    if view.started:
        seconds = max(0, int(now.timestamp() - view.started))
        lines.append(f"Elapsed: {seconds // 60}m {seconds % 60:02d}s")
    if view.updated and now.timestamp() - view.updated > STALE_S and not view.terminal:
        lines.append("Last progress checkpoint is over 3 minutes old")
    lines.append("Updated " + now.astimezone().strftime("%H:%M:%S %Z"))
    return "\n".join(lines)[:1800]
