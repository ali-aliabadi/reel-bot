# reel-bot

A pipeline that writes, voices, renders and (later) posts short-form vertical
videos. ali picks one angle per video from Telegram; reel-bot does the rest.

> **Status:** October 2026 experiment. Three YouTube Shorts channels (data charts,
> history stories, AI-tools news) run on the same pipeline; the best one is kept on
> 4 Nov. See [docs/ROADMAP.md](docs/ROADMAP.md) for progress and
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Why a human pick

YouTube, TikTok and Meta demonetize AI videos made with no human input. reel-bot
automates the production but asks ali, through [Relay](https://github.com/ali-aliabadi/relay),
to choose each video's angle, and records that choice with the video.

## Running locally

Needs macOS or Linux with [uv](https://docs.astral.sh/uv/) and ffmpeg.

```bash
brew install uv ffmpeg     # once
make tools                 # Python 3.12, locked dev tools, gitleaks
cp .env.example .env       # then fill in the keys
make run ARGS=version
make check                 # before every commit
```

## Contributing

Branch from `master`, open a PR, keep it to one roadmap item, and run `make check`.
Agents: read [CLAUDE.md](CLAUDE.md) first.
