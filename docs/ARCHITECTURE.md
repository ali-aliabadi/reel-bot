# reel-bot architecture

This is the design reference for reel-bot. CLAUDE.md summarizes it for agents;
docs/ROADMAP.md tracks what is built. When the design changes, update this file
in the same PR.

## What reel-bot is

A Python CLI that turns an old fable into a finished vertical video for one
adult-framed fables channel (Kalilah and Dimnah, Panchatantra, Rumi, Aesop),
narrated by Tara. ali picks the story; Tara records the narration; reel-bot does
the rest. It runs on ali's Mac (later on a schedule), keeps a log of every video
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
| script | chosen story | script with hook, paragraphs, picture prompts, source | Claude, retold from a public-domain translation |
| voice | script | Tara's recording, cleaned and loudness-normalized | Needs voice-note answers in Relay (proposed, not built) |
| captions | voice | word timings and caption track | Whisper on the Mac; burned in |
| visuals | script, timings | one picture per paragraph | Image source pending ali's choice (OpenAI API or FLUX on the Mac) |
| render | everything above | 1080x1920 H.264 MP4, 30 fps | ffmpeg, slow zoom per picture, cuts at pauses |
| review | render | Relay message with a 720p preview | Relay files are capped at 5 MB |
| publish | render, metadata | platform video ID | Manual until the YouTube API audit passes |

## Data

SQLite at `./data/reel-bot.db` (`REEL_BOT_DB_PATH`). Tables, added by roadmap items:

- `stories`: the catalog (id, tale, title, collection, source, source_ref), synced from `src/reel_bot/data/stories.json`.
- `tales_used`: one row per tale ever told (tale is the primary key: the no-repeat rule), with the story, video ID and time.
- `videos`: id, story, story options, chosen_by, chosen_at, framing, stage outputs, human minutes, cost_usd, platform video ID, posted_at.
- `assets`: video id, kind (music, image, footage, font), source URL, licence.
- `metrics`: video id, read_at, views, average percentage viewed, subscribers gained, viewed vs swiped (entered by hand from Studio).

`reel-bot stories` lists the stories whose tale is still unused, `reel-bot stories pick` suggests 3 from different tales, and `reel-bot stories use <story id> <video id>` records a tale as told (refused if it already was). Record the hand-made videos this way too.

Renders go to `./out/<video id>/`, which is git-ignored.

## Configuration

| Variable | Required | Default | What |
| --- | --- | --- | --- |
| `RELAY_URL` | yes | | Relay's base URL, `https` only |
| `RELAY_API_KEY` | yes | | reel-bot's Relay key (`relay clients create reel-bot`) |
| `RELAY_APP` | no | `reel-bot` | Sent as every message's `source` |
| `RELAY_USER` | no | `admin` | Who gets the messages |
| `REEL_BOT_DB_PATH` | no | `./data/reel-bot.db` | SQLite file |
| `REEL_BOT_OUT_DIR` | no | `./out` | Render output |

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
