# Roadmap

Checklist of work for reel-bot. Agents: take the first unchecked item in the
current phase, and tick it (`[x]`) in the same PR that completes it. Each item
should be one small PR unless noted. Design details are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Phase 0: one-time setup (ali, by hand)

- [ ] Add the GitHub remote to the local checkout and push the first commit to `master`
- [ ] Protect `master` (require PRs and the `make check` status)
- [ ] Install `uv` and `ffmpeg` on the Mac (`brew install uv ffmpeg`), then `make tools`
- [ ] Create a Relay client: `relay clients create reel-bot`; put the key in `.env` (never in git)
- [ ] Create three YouTube channels (brand accounts) for arms A, B and C
- [ ] Create a Google Cloud project, enable YouTube Data API v3 and YouTube Analytics API, and file the API audit for uploads
- [ ] Anthropic API key in `.env` for scripts
- [ ] Pick the TTS provider (one voice per arm) and put its key in `.env`

## Phase 1: skeleton and tooling

- [x] `pyproject.toml` with uv, `src/reel_bot` package, `reel-bot version`
- [x] Makefile with `tools run fmt fmt-check lint test sec check build`
- [x] ruff, pyright strict, pytest with coverage, pip-audit, actionlint
- [x] `scripts/check-file-length.sh` (300 / 500 for tests) wired into `make lint`
- [x] gitleaks pinned by checksum, in `make sec`
- [x] `.gitignore`, `.env.example` (no real values), `.editorconfig`, `.python-version`
- [x] `.github/workflows/ci.yml` (`make check`), actions pinned by SHA; Dependabot for uv and Actions
- [x] PR template; `.claude/settings.json` allowing the `make` targets
- [x] `reel-bot-review` project skill; `skills/relay-notify` copied from relay
- [ ] `config.py`: parse the ARCHITECTURE config table from env, fail fast on missing required values
- [ ] JSON logging setup with a redaction helper for keys and Relay bodies
- [ ] SessionStart hook so cloud sessions get uv, ffmpeg and gitleaks

## Phase 2: one video end to end (arm A first, manual pick)

- [ ] SQLite store: open helper, `videos` and `assets` tables, migrations run on startup
- [ ] Video IDs (`vid_` + ULID) and the on-disk cache under `out/<id>/`
- [ ] `script` stage: Claude call from a chosen angle; snapshot tests with a fake client
- [ ] `voice` stage behind a small interface, with a fake for tests
- [ ] `captions` stage from word timings
- [ ] `render` stage: ffmpeg 1080x1920 30 fps; render smoke test with ffprobe (skips without ffmpeg)
- [ ] Arm A `visuals`: animated chart from a CSV
- [ ] `reel-bot make --arm A --angle "..."` produces a finished MP4 and logs cost and time

## Phase 3: the daily loop through Relay

- [ ] `angles` stage per arm: 3 candidates from the arm's sources
- [ ] Morning run: one Relay `question` per arm; record the pick (who, when, which); skip the arm at 11:00 with no answer
- [ ] Arm B `visuals` (AI images, slow pans) and fact sources per claim
- [ ] Arm C `visuals` (screenshots and captions) and the item's source URL
- [ ] "Render ready" Relay message per video; failure alert with stage and last error lines
- [ ] Scheduled run on the Mac (launchd) at a fixed hour

## Phase 4: metrics and the decision

- [ ] `metrics` table; nightly YouTube Analytics pull for videos at 72 hours
- [ ] `reel-bot metrics add` for viewed-vs-swiped copied from Studio
- [ ] Morning Relay report: one table per arm (videos, median views, retention, subs per 1k)
- [ ] 25 Oct cut check report and 4 Nov decision report from the same data

## Later (after 4 Nov, for the winning arm)

- [ ] Auto-publish to YouTube once the API audit passes, still gated on a recorded pick
- [ ] TikTok cuts over 60 seconds and the Content Posting API (after its audit)
- [ ] Run on the VPS with a deploy workflow like relay's
- [ ] A/B hooks inside the winning arm
