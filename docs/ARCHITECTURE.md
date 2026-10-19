# reel-bot architecture

This is the design reference for reel-bot. CLAUDE.md summarizes it for agents;
docs/ROADMAP.md tracks what is built. When the design changes, update this file
in the same PR.

## What reel-bot is

A Python CLI that turns an old fable into a finished vertical video for one
adult-framed fables channel (Kalilah and Dimnah, Panchatantra, Rumi, Aesop),
narrated by Tara. ali picks the story; Tara records the narration; reel-bot does
the rest. It runs on ali's VPS (next to Relay; later on a schedule), keeps a log of every video
in SQLite, and talks to ali and Tara through Relay.

## Principles

- **One human pick per video.** The platforms demonetize content with no human input. The pick is recorded and required before publishing. Tara's narration is the second human part.
- **No tale told twice.** The story catalog groups versions of the same tale (the lion and the hare is in both Kalilah and the Panchatantra); once a tale is used, every version of it is spent. The database enforces this.
- **Same-age comparisons.** Every metric is read 72 hours after a video goes up.
- **Cheap to rerun.** Stage outputs are cached on disk by video ID; a rerun never pays twice.
- **Personal scale.** One SQLite file, no queues or servers in October.
- **Few dependencies.** Prefer the standard library. Each new dependency needs a reason.

## The experiment

The first plan (three channels: charts, history stories, AI news) was replaced on
13 Oct by one fables channel; the research is in the
[research doc](https://claude.ai/code/artifact/2dc784e9-033b-4a0d-a184-e454eec81337).
Inside the channel we test framing (fable alone vs fable plus a short work
lesson) and length (about 45 s vs 61 to 90 s). First public post 22 Oct; the
first videos are made by hand while the pipeline is built.

## Pipeline

```
stories ──► [Relay question: ali taps one of 3] ──► script ──► [Relay: script to Tara, she replies with a voice note] ──► voice
   ──► captions ──► visuals ──► render ──► review ──► publish
the video row in SQLite records every stage's output path, cost and timing
metrics (nightly): YouTube Analytics ──► SQLite ──► Relay morning report
```

| Stage | In | Out | Notes |
| --- | --- | --- | --- |
| stories | catalog, tales already used | 3 candidates from different unused tales | One Relay `question`; the pick is recorded |
| script | chosen story | `script.json`: hook, 3-6 paragraphs each with a picture prompt, the lesson (lesson framing only), source | Claude Opus 5.5 with structured output and refusal fallback; the checker (word budget by length, paragraphs, hook, lesson by framing) allows one retry with its feedback; cost recorded on the stage row |
| voice | script, Tara's recording | `voice.wav`: 48 kHz mono, rumble and hiss removed, silence trimmed at both ends, loudness -14 LUFS | ffmpeg; a take outside the length's expected range (short 35-60 s, long 55-100 s) imports with a warning; a new take replaces the old |
| captions | script, voice | `captions.json`: each script word with its time, short caption cues (up to 3 words or 16 characters, split at punctuation and pauses), and when each part (hook, paragraphs, lesson) is spoken | OpenAI transcription (`whisper-1`, word timestamps, about $0.006 a minute); the script's words are kept and take their times from the matching heard words, unmatched ones are spread across the gap; under 50% matched is refused as the wrong take, under 85% warns; the render stage burns the cues in |
| visuals | script, timings | one picture per paragraph | OpenAI image API |
| render | everything above | 1080x1920 H.264 MP4, 30 fps | ffmpeg, slow zoom per picture, cuts at pauses |
| review | render | Relay message with a 720p preview | Relay files are capped at 5 MB |
| publish | render, metadata | platform video ID | Manual until the YouTube API audit passes |

## Data

SQLite at `./data/reel-bot.db` (`REEL_BOT_DB_PATH`). Tables, added by roadmap items:

- `stories`: the catalog (id, tale, title, collection, source, source_ref), synced from `src/reel_bot/data/stories.json`.
- `tales_used`: one row per tale ever told (tale is the primary key: the no-repeat rule), with the story, video ID and time.
- `videos`: id (`vid_` + ULID), created_at, framing (`fable` or `lesson`), length (`short` or `long`), story_options (JSON list offered to ali), story_id, chosen_by, chosen_at. A CHECK makes the pick all or nothing; choosing also spends the tale in `tales_used`, in one transaction, and must be one of the options when options were offered. Platform video ID and posted_at come with publishing.
- `video_stages`: one row per (video, stage): output path relative to `out/<video id>/`, cost_usd, seconds, finished_at. A rerun reuses the output instead of paying again.
- `assets`: video id, kind (music, image, footage, font), source URL, licence.
- `metrics`: video id, read_at, views, average percentage viewed, subscribers gained, viewed vs swiped (entered by hand from Studio).

`reel-bot stories` lists the stories whose tale is still unused, `reel-bot stories pick` suggests 3 from different tales, and `reel-bot stories use <story id> <video id>` records a tale as told (refused if it already was). Record the hand-made videos this way too.

`reel-bot video new --framing fable --length short` creates a video and prints its ID, `reel-bot video choose <video id> <story id> --by ali` records the human pick, and `reel-bot video show <video id>` prints the settings, the pick and each finished stage.

`reel-bot script <video id>` writes `out/<video id>/script.json` once the story is picked; a rerun reuses it. Word budgets: short 95-120 spoken words, long 150-200 (Tara reads about 2.3 words a second).

`reel-bot captions <video id>` writes `out/<video id>/captions.json` once the take is imported; a rerun reuses it.

`reel-bot voice <video id> <recording>` cleans Tara's take into `out/<video id>/voice.wav` once the script exists. The Relay flow (phase 3) will call the same import with the voice note it downloads.

Each video's stage outputs go to `./out/<video id>/` (`videos.video_dir`, which accepts only a valid video ID), git-ignored.

## Configuration

| Variable | Required | Default | What |
| --- | --- | --- | --- |
| `RELAY_URL` | yes | | Relay's base URL, `https` only |
| `RELAY_API_KEY` | yes | | reel-bot's Relay key (`relay clients create reel-bot`) |
| `RELAY_APP` | no | `reel-bot` | Sent as every message's `source` |
| `RELAY_USER` | no | `admin` | Who gets the messages |
| `REEL_BOT_DB_PATH` | no | `./data/reel-bot.db` | SQLite file |
| `REEL_BOT_OUT_DIR` | no | `./out` | Render output |
| `ANTHROPIC_API_KEY` | for `script` | | Claude API key; read by the SDK, never logged |
| `OPENAI_API_KEY` | for `captions` | | OpenAI API key (transcription, later pictures); sent only in the request header, never logged |
| `REEL_BOT_LOG_LEVEL` | no | `info` | `debug`, `info`, `warning` or `error`; JSON lines on stderr, keys and Relay bodies redacted |

`reel-bot config` (or `make run ARGS=config`) checks these and prints them without the key. Model, voice and YouTube credentials are added to this table by the roadmap items that need them.

## Quality tooling

Everything runs through the Makefile, locally and in CI. Python tools are pinned in
`uv.lock`; gitleaks is pinned by version and checksum in the Makefile and
`scripts/install-gitleaks.sh`.

- **ruff** (lint and format) with, beyond the defaults: bugbear, bandit (`S`), pylint, simplify, pyupgrade, `T20` (no `print` outside `cli.py`), complexity 10.
- **pyright** in strict mode.
- **File length:** `scripts/check-file-length.sh` fails on `.py` files over 300 lines (tests 500).
- **Other checks:** `uv lock --check`, `actionlint` for workflows, `pip-audit`, `gitleaks` over the history.
- **CI** (`ci.yml`): `make check` on every PR and push to `master`; actions pinned by SHA; `contents: read` only.
- **Dependabot:** weekly grouped updates for uv and GitHub Actions.
