# Roadmap

Checklist of work for reel-bot. Agents: take the first unchecked item in the
current phase, and tick it (`[x]`) in the same PR that completes it. Each item
should be one small PR unless noted. Design details are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Phase 0: one-time setup (ali, by hand)

- [ ] Add the GitHub remote to the local checkout and push the first commit to `master`
- [ ] Protect `master` (require PRs and the `make check` status)
- [ ] Check the VPS's memory before installing: `vmware-toolbox-cmd stat balloon` and `grep -E "Slab|SUnreclaim" /proc/meminfo`
- [ ] On the VPS: clone to `/opt/reel-bot`, install `uv`, `ffmpeg` and the caption font (`apt install ffmpeg fonts-dejavu-core`), then `make tools` and `make models`
- [ ] Create a Relay client: `relay clients create reel-bot`; put the key in `.env` (never in git)
- [x] Create the fables channel on the work Gmail (one channel, not three)
- [ ] Create a Google Cloud project, enable YouTube Data API v3 and YouTube Analytics API, add a "Desktop app" OAuth client, set the consent screen to In production, then `reel-bot auth youtube`; file the upload audit
- [ ] Instagram: switch the account to Creator or Business, create a Meta app (Instagram API with Instagram Login), add the account as Instagram Tester, generate a token, `reel-bot auth instagram`
- [ ] TikTok: create an app with Login Kit and the Content Posting API (Direct Post), register the redirect URI, `reel-bot auth tiktok`; apply for the audit
- [ ] Anthropic API key in the VPS's `.env` for scripts
- [x] Choose the picture source: Tara makes them in ChatGPT from reel-bot's prompts (free; the OpenAI image API was dropped to keep costs at zero)

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
- [x] `captions` stage: word timings from Whisper on the machine (`faster-whisper`, free) aligned to the script, caption cues and part timings (burned in by `render`)
- [x] `visuals` stage: one prompt per paragraph; Tara answers each with her picture through Relay (`reply: "image"`), or ali adds it by hand; each picture recorded in `assets`
- [x] `render` stage: ffmpeg 1080x1920 30 fps, slow zoom, cuts at the part timings, captions burned in, at low priority on 2 threads; render smoke test with ffprobe (skips without ffmpeg)
- [x] `reel-bot make <video id> [--voice <file>]` runs every stage the video is ready for, stops for the pick and for Tara's take, and prints cost and time per stage
- [x] Quality to Relay's level: odd-input tests for every parser, timeout tests for every process and call, a 95% coverage floor, and a monthly spend cap (`REEL_BOT_MONTHLY_CAP_USD`) on every paid call

## Phase 3: the daily loop through Relay

- [x] Relay: voice-note answers to a question (relay PR #23; up to 20 MB, Telegram's cap)
- [x] Morning run: 3 story candidates as a Relay `question` to ali; record the pick (who, when, which)
- [x] Send the script to Tara through Relay and wait for her voice-note answer
- [x] The finished MP4 to Tara and ali through Relay (she uploads by hand); failure alert with the step and the error, `reel-bot daily retry`
- [ ] Relay: file blocks up to 45 MB (Relay PR), `RELAY_MAX_BODY_BYTES=67108864` in Relay's `.env`, and reel-bot's `RELAY_URL=http://127.0.0.1:18080` on the VPS, so the full video fits
- [x] Scheduled run on the VPS (systemd timer) at a fixed hour, under a memory cap (`MemoryMax`) so Relay and the other services survive a bad render (units in `deploy/systemd`)
- [ ] ali: set `REEL_BOT_NARRATOR` in the VPS's `.env` and install the timers (`deploy/README.md`)
- [ ] Grow the catalog: every tale in the four public-domain sources, with source references

## Phase 4: metrics and decisions

- [ ] `metrics` table; nightly YouTube Analytics pull for videos at 72 hours
- [ ] `reel-bot metrics add` for viewed-vs-swiped copied from Studio
- [ ] Morning Relay report: videos, median views, retention, subs per 1k, split by framing and length
- [ ] Weekly report comparing framing (fable alone vs with a lesson) and length (45 s vs 61 to 90 s)

## Later

- [x] `publish` stage: YouTube, Instagram Reels and TikTok uploads, gated on a recorded pick (also in the database), AI label set, one post per platform, `reel-bot auth` and `reel-bot publish`
- [ ] Public on YouTube and TikTok once each audit passes (`TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE`)
- [ ] Run on the VPS with a deploy workflow like relay's
- [ ] A/B hooks
