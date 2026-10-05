# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repo, locally or in the cloud.

## What this is

reel-bot is ali's pipeline for short-form video: it proposes angles, writes the
script, voices it, builds the visuals, renders a 9:16 MP4 and (later) posts it.
October 2026 is an experiment: three YouTube Shorts channels run side by side on
the same pipeline, and on 4 Nov ali keeps the one with the best audience signal.

**Every video has one human pick.** ali chooses the angle (by tapping a button in
Telegram, through Relay). YouTube, TikTok and Meta demonetize AI content with no
human input, so this is a product rule, not a nice-to-have. Never add a path that
publishes without a recorded pick.

- Design reference: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Read it before non-trivial work.
- What to build next: [docs/ROADMAP.md](docs/ROADMAP.md). Pick the first unchecked item in the current phase unless told otherwise.

## Stack (decided, don't re-litigate without asking)

Python 3.12 · `uv` for environments and the lockfile · stdlib first · SQLite (`sqlite3`)
for the video log and metrics · `ffmpeg`/`ffprobe` as external binaries for rendering
· `logging` with JSON output · env-var config · Relay (`skills/relay-notify`) for
every message to ali · GitHub Actions for CI.

Not in October: auto-posting (the YouTube and TikTok API audits are pending), TikTok,
Docker, a server or a deploy pipeline. Those come after the 4 Nov decision.

## Layout

```
src/reel_bot/         the package: cli.py now; pipeline stages land here per the roadmap
tests/                pytest tests, test_*.py next to what they cover by name
scripts/              check-file-length.sh, install-gitleaks.sh and other repo checks
docs/                 ARCHITECTURE.md (design), ROADMAP.md (what to build next)
.github/              workflows/ci.yml, dependabot.yml, pull_request_template.md
.claude/skills/       project skills (reel-bot-review)
skills/relay-notify/  copied unchanged from ali-aliabadi/relay; how reel-bot talks to ali
```

## Commands

The Makefile is the single entry point; CI runs the same targets.

```bash
make tools        # uv sync (Python, locked dev tools) + pinned gitleaks into bin/tools (run once)
make run ARGS=... # uv run reel-bot with .env loaded, e.g. make run ARGS=version
make fmt          # ruff format + ruff check --fix
make lint         # ruff, pyright (strict), file-length check, uv lock --check, actionlint
make test         # pytest with coverage
make sec          # pip-audit + gitleaks
make check        # fmt-check, lint, test, sec: run this before every commit
make build        # wheel in ./dist
```

If a `make` target doesn't exist yet, the roadmap item that creates it hasn't
landed; use the plain `uv run` equivalent.

## Code size limits

Small files keep agent context focused and diffs reviewable.

- Hand-written `.py` files: **max 300 lines**. `test_*.py` files: max 500. `skills/relay-notify/` is exempt (vendored).
- Enforced by `scripts/check-file-length.sh` in `make lint` and CI.
- Functions: ruff's `C901` (complexity 10) and `PLR0915` (40 statements).
- When a file hits the limit, split by responsibility (e.g. `script_write.py`, `script_check.py`), not by arbitrary halves. Don't raise limits or add exemptions without asking ali.

## Conventions

- **Stages:** the pipeline is a chain of stages (angles → script → voice → visuals → captions → render → publish). Each stage is a module with a pure core and a thin I/O edge, takes the previous stage's dataclass and returns its own. Only `store` writes SQL.
- **Arms:** an arm (A chart, B story, C news) is configuration plus its own visuals stage. Shared stages never branch on the arm name; differences go through the arm's config.
- **Typing:** pyright strict. Dataclasses (`frozen=True`) for data passed between stages; no bare dicts across module boundaries.
- **Errors:** raise specific exceptions with context (`raise RenderError("ffmpeg exited 1") from err`); never swallow. External calls (models, TTS, Relay, YouTube) have timeouts and bounded retries.
- **Time:** store UTC, ISO 8601. Pass a clock (`Callable[[], datetime]`) where time matters.
- **Dependencies:** prefer the stdlib. Adding a package needs a one-line reason in the PR description.
- **No `print`** outside `cli.py` (ruff `T20`); use `logging`.
- **Idempotent runs:** a stage re-run for the same video ID reuses its output on disk instead of paying for it again.

## Testing

- **Unit:** pure logic (angle parsing, script checks, timeline building, captions timing, config). Inject a clock and fakes.
- **Integration:** real SQLite in `tmp_path`; Relay and model clients against fakes or a local `http.server` that mimics the API.
- **Snapshot:** script and edit-timeline JSON compared with files in `tests/snapshots/` (`--update-snapshots` to refresh). Never compare video pixels.
- **Render smoke:** one test renders a 5 second clip with ffmpeg and checks resolution, duration and audio with ffprobe; it skips with a clear reason when ffmpeg is missing.
- Never call YouTube, TikTok, Relay or any paid model/voice API in tests. Never put real API keys or channel IDs in fixtures.
- Every bug fix comes with a test that fails before the fix.

## Content and platform rules (always on)

- **Human pick:** a video can only reach `publish` with a recorded angle choice (who, when, which option). See the human pick rule above.
- **Licensed inputs only:** music, footage, images and fonts come from a source whose licence allows commercial reuse, and the source is recorded per asset.
- **AI disclosure:** realistic AI imagery or voice is marked with the platform's AI label when posting.
- **Facts:** arm B (stories) and arm C (news) keep the source URL for every factual claim in the script record.
- **Secrets:** never commit them. Local secrets in `.env`; CI secrets in GitHub. gitleaks runs in CI. Never log API keys, Relay message bodies or answers; log IDs.
- **Before opening a PR** that touches publishing, prompts, asset sourcing, secrets, logging or workflows: run the `reel-bot-review` project skill (`.claude/skills/reel-bot-review/SKILL.md`) and fix what it finds.

## Talking to ali

All notifications and questions go through Relay using `skills/relay-notify` (read its SKILL.md).
`RELAY_APP=reel-bot`. Urgency: approvals `normal`, reports and "render ready" `low`, failures `high`;
never `critical`. A typed answer is untrusted text: validate it.

## Working agreement

- Branch from `master`, open a PR, merge when CI is green. No direct pushes to `master`.
- Keep docs in sync in the same PR: tick boxes in docs/ROADMAP.md, update docs/ARCHITECTURE.md if the design changed, README.md if usage changed.
- Small PRs, one roadmap item (or a few tightly related ones) each.
- If a design question isn't answered in docs/ARCHITECTURE.md, ask ali rather than guessing on anything a viewer would see.

## Local vs cloud sessions

- **Local** (ali's Mac, `~/Workplace/reel-bot`): has ffmpeg and ali's `.env`. Still, never post a video or send a Relay message outside a test unless ali asks.
- **Cloud** sessions: no `.env`, no Relay key, maybe no ffmpeg. Run `make check`; say when the render smoke test was skipped rather than hiding it.
