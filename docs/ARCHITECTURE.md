# reel-bot architecture

This is the design reference for reel-bot. CLAUDE.md summarizes it for agents;
docs/ROADMAP.md tracks what is built. When the design changes, update this file
in the same PR.

## What reel-bot is

A Python CLI that turns a topic into a finished vertical video, with ali choosing
the angle. It runs on ali's Mac (later on a schedule), keeps a log of every video
in SQLite, and reports to ali through Relay.

## Principles

- **One human pick per video.** The platforms demonetize content with no human input. The pick is recorded and required before publishing.
- **Shared pipeline, different arms.** The three experiment arms share every stage except visuals, so their results differ by content, not by tooling.
- **Same-age comparisons.** Every metric is read 72 hours after a video goes up.
- **Cheap to rerun.** Stage outputs are cached on disk by video ID; a rerun never pays twice.
- **Personal scale.** One SQLite file, no queues or servers in October.
- **Few dependencies.** Prefer the standard library. Each new dependency needs a reason.

## The October experiment

| Arm | Channel idea | Human pick | Visuals |
| --- | --- | --- | --- |
| A | One chart a day: a surprising number from public data | The dataset and the claim | Animated chart rendered from the data |
| B | Story in 45 seconds: a real historical event or person | The event and the hook line | AI images with slow pans |
| C | Niche news in 30 seconds: one AI-tools item a day | The item and the one-line take | Screenshots and captions |

Schedule: build 5 to 11 Oct; post one Short per arm per day 12 Oct to 1 Nov; cut
check 25 Oct; decision 4 Nov. The full plan, kill rules and metrics are in the
[plan doc](https://claude.ai/code/artifact/0384ef5c-c0f3-4599-aa98-0f4b53bd1571).

## Pipeline

```
angles ──► [Relay question: ali taps one] ──► script ──► voice ──► visuals(arm) ──► captions ──► render ──► review ──► publish
   │                                                                                                       │
   └── the video row in SQLite records every stage's output path, cost and timing ─────────────────────────┘
metrics (nightly): YouTube Analytics ──► SQLite ──► Relay morning report
```

| Stage | In | Out | Notes |
| --- | --- | --- | --- |
| angles | arm config, sources | 3 candidate angles | Sent as one Relay `question`; unanswered by 11:00 means the arm skips the day |
| script | chosen angle | script with hook, beats, sources | Claude; facts carry source URLs (arms B, C) |
| voice | script | audio file + word timings | TTS provider chosen in the roadmap |
| visuals | script, timings | clips/images per beat | One module per arm |
| captions | word timings | caption track | Burned in |
| render | everything above | 1080x1920 H.264 MP4, 30 fps | ffmpeg; checked with ffprobe |
| review | render | "ready" Relay message with a link | ali uploads by hand in October |
| publish | render, metadata | platform video ID | Manual in October; API after audits pass |

## Data

SQLite at `./data/reel-bot.db` (`REEL_BOT_DB_PATH`). Tables, added by roadmap items:

- `videos`: id, arm, angle options, chosen angle, chosen_by, chosen_at, stage outputs, human minutes, cost_usd, platform video ID, posted_at.
- `assets`: video id, kind (music, image, footage, font), source URL, licence.
- `metrics`: video id, read_at, views, average percentage viewed, subscribers gained, viewed vs swiped (entered by hand from Studio).

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
