# Roadmap

Checklist of work for reel-bot. Agents: take the first unchecked item in the
current phase, and tick it (`[x]`) in the same PR that completes it. Each item
should be one small PR unless noted. Design details are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Phase 0: one-time setup (ali, by hand)

- [ ] Add the GitHub remote to the local checkout and push the first commit to `master`
- [ ] Protect `master` (require PRs and the `make check` status)
- [ ] Check the VPS's memory before installing: `vmware-toolbox-cmd stat balloon` and `grep -E "Slab|SUnreclaim" /proc/meminfo`
- [ ] On the VPS: clone to `/opt/reel-bot`, install `uv`, `ffmpeg` and the caption font (`apt install ffmpeg fonts-dejavu-core`), then `make tools`
- [ ] Create a Relay client: `relay clients create reel-bot`; put the key in `.env` (never in git)
- [x] Create the fables channel on the work Gmail (one channel, not three)
- [ ] Create a Google Cloud project and enable YouTube Data API v3 and YouTube Analytics API; file the upload audit once the upload flow works
- [ ] Anthropic API key in the VPS's `.env` for scripts
- [x] Choose the picture source: OpenAI image API
- [ ] OpenAI API key, with a monthly limit, in the VPS's `.env` (captions and pictures)

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
- [x] `config.py`: parse the ARCHITECTURE config table from env, fail fast on missing required values; `reel-bot config` to check `.env`
- [x] JSON logging setup with a redaction helper for keys and Relay bodies
- [ ] SessionStart hook so cloud sessions get uv, ffmpeg and gitleaks

## Phase 2: one video end to end (manual pick)

- [x] Story catalog with versions grouped by tale; SQLite store; `reel-bot stories` (list, pick, use); no tale told twice
- [x] Video IDs (`vid_` + ULID), the `videos` table and the on-disk cache under `out/<id>/`
- [x] `script` stage: Claude call from a chosen story; snapshot tests with a fake client
- [x] `voice` stage: import a recording, clean it and normalize loudness (ffmpeg)
- [x] `captions` stage: word timings from OpenAI transcription aligned to the script, caption cues and part timings (burned in by `render`)
- [x] `visuals` stage: OpenAI image API behind a small interface, with a fake for tests; each picture recorded in `assets`
- [x] `render` stage: ffmpeg 1080x1920 30 fps, slow zoom, cuts at the part timings, captions burned in, at low priority on 2 threads; render smoke test with ffprobe (skips without ffmpeg)
- [ ] `reel-bot make --story <id> --voice <file>` produces a finished MP4 and logs cost and time

## Phase 3: the daily loop through Relay

- [x] Relay: voice-note answers to a question (relay PR #23; up to 20 MB, Telegram's cap)
- [ ] Morning run: 3 story candidates as a Relay `question` to ali; record the pick (who, when, which)
- [ ] Send the script to Tara through Relay and wait for her voice-note answer
- [ ] "Render ready" Relay message with a 720p preview; failure alert with stage and last error lines
- [ ] Scheduled run on the VPS (systemd timer) at a fixed hour, under a memory cap (`MemoryMax`) so Relay and the other services survive a bad render
- [ ] Grow the catalog: every tale in the four public-domain sources, with source references

## Phase 4: metrics and decisions

- [ ] `metrics` table; nightly YouTube Analytics pull for videos at 72 hours
- [ ] `reel-bot metrics add` for viewed-vs-swiped copied from Studio
- [ ] Morning Relay report: videos, median views, retention, subs per 1k, split by framing and length
- [ ] Weekly report comparing framing (fable alone vs with a lesson) and length (45 s vs 61 to 90 s)

## Later

- [ ] Auto-publish to YouTube once the API audit passes, still gated on a recorded pick
- [ ] TikTok and Instagram Reels, and the TikTok Content Posting API (after its audit)
- [ ] Run on the VPS with a deploy workflow like relay's
- [ ] A/B hooks
