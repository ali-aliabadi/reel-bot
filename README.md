# reel-bot

A pipeline that turns old fables (Kalilah and Dimnah, Panchatantra, Rumi, Aesop)
into short vertical videos. ali picks the story from Telegram, Tara narrates it,
and reel-bot does the rest. No tale is told twice.

> **Status:** one YouTube Shorts fables channel, first public post 22 Oct 2026;
> the first videos are made by hand while the pipeline is built. See [docs/ROADMAP.md](docs/ROADMAP.md) for progress and
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Why a human pick

YouTube, TikTok and Meta demonetize AI videos made with no human input. reel-bot
automates the production but asks ali, through [Relay](https://github.com/ali-aliabadi/relay),
to choose each video's story, and records that choice with the video. Tara's
narration is the other human part.

## Running locally

Needs macOS or Linux with [uv](https://docs.astral.sh/uv/) and ffmpeg.

```bash
brew install uv ffmpeg     # once (on the VPS: apt install ffmpeg, and uv's installer)
make tools                 # Python 3.12, locked dev tools, gitleaks
cp .env.example .env       # then fill in the keys
make run ARGS=config    # checks .env, never prints the key
make run ARGS="video new --framing fable --length short"   # prints a video ID
make run ARGS="video choose VIDEO_ID STORY_ID --by ali"
make run ARGS="script VIDEO_ID"   # Claude writes out/VIDEO_ID/script.json
make run ARGS="voice VIDEO_ID ~/Downloads/tara.ogg"   # cleans her take into voice.wav
make run ARGS="captions VIDEO_ID"   # word timings and caption cues
make check                 # before every commit
```

## Contributing

Branch from `master`, open a PR, keep it to one roadmap item, and run `make check`.
Agents: read [CLAUDE.md](CLAUDE.md) first.
