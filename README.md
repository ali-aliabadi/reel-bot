# reel-bot

A pipeline that turns old fables (Kalilah and Dimnah, Panchatantra, Rumi, Aesop)
into short vertical videos. ali picks the story from Telegram, a Kokoro voice
narrates it (Tara can again with `REEL_BOT_NARRATION=tara`), and reel-bot does the rest. No tale is told twice.

> **Status:** one YouTube Shorts fables channel, first public post 22 Oct 2026;
> the first videos are made by hand while the pipeline is built. See [docs/ROADMAP.md](docs/ROADMAP.md) for progress and
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Why a human pick

YouTube, TikTok and Meta demonetize AI videos made with no human input. reel-bot
automates the production but asks ali, through [Relay](https://github.com/ali-aliabadi/relay),
to choose each video's story, and records that choice with the video. Tara's
script and pictures from ChatGPT are the other human part.

## Running locally

Needs macOS or Linux with [uv](https://docs.astral.sh/uv/) and ffmpeg.

```bash
brew install uv ffmpeg     # once (on the VPS: apt install ffmpeg fonts-dejavu-core, and uv's installer)
make tools                 # Python 3.12, locked dev tools, gitleaks
make models                # Whisper (captions) and Kokoro (the narration) into models/ (about 270 MB)
cp .env.example .env       # then fill in the keys
make run ARGS=config    # checks .env, never prints the key
make run ARGS="video new --framing fable --length short"   # prints a video ID
make run ARGS="video choose VIDEO_ID STORY_ID --by ali"
make run ARGS="script VIDEO_ID"   # Claude writes out/VIDEO_ID/script.json
make run ARGS="voice VIDEO_ID ~/Downloads/tara.ogg"   # cleans her take into voice.wav
make run ARGS="make VIDEO_ID"       # script and read-this.txt for Tara; then waits for her
make run ARGS="visuals VIDEO_ID"    # the picture prompts still missing; --add N FILE adds one
make run ARGS="make VIDEO_ID --voice ~/Downloads/tara.ogg"   # finishes render.mp4
make run ARGS="daily start --framing fable --length short"   # asks ali in Relay to pick a story
make run ARGS="daily tick"          # pick -> ChatGPT script via Tara -> Kokoro's voice -> her pictures -> video
make run ARGS="daily list"          # daily videos not yet sent, and where each is
make run ARGS="daily cancel VIDEO_ID"   # or --all: cancel days that won't happen (tells Tara)
make run ARGS="auth youtube"        # once per platform: youtube, instagram, tiktok
make run ARGS="publish VIDEO_ID"    # posts to every signed-in platform (or add --publish to make)
# or one stage at a time:
make run ARGS="visuals VIDEO_ID"    # one picture per paragraph (any time after the script)
make run ARGS="captions VIDEO_ID"   # word timings and caption cues
make run ARGS="render VIDEO_ID"     # out/VIDEO_ID/render.mp4, 1080x1920 with burned-in captions
make check                 # before every commit
```

## Contributing

Branch from `master`, open a PR, keep it to one roadmap item, and run `make check`.
Agents: read [CLAUDE.md](CLAUDE.md) first.

New story briefs request a punch-line ending and 6-9 illustrated scenes for
short videos or 10-14 for long ones (up to 90 seconds). Kokoro reads faster,
with shorter pauses; ffmpeg adds gentle zooms and short dissolves locally.
Already cached videos keep their existing script, audio and render.

Cloudflare drafts use the approved option D: Klein 4B at 1024×1024 with a
detailed ink-and-gouache manuscript style, aged parchment and muted earth tones.
Existing cached drafts are reused with their original model attribution.

Picture prompts in Relay are copyable code blocks, including alongside a draft;
use Telegram's code-block Copy action (client support may vary). Paste into
Gemini AI Studio or ChatGPT and reply with the image file.

To test Gemini's image API separately, put `GEMINI_API_KEY` in the VPS's `.env`
using a key from a project **with billing disabled**, then run from the checkout:

```sh
set -a; . ./.env; set +a
uv run python -m reel_bot.cli_gemini_probe --billing-disabled \
  --model gemini-3.1-flash-image --prompt-file /tmp/image-prompt.txt --out /tmp/gemini-test
```

The probe sends one request with a 90-second timeout, no retries, and writes the
returned picture with its detected extension. It refuses an existing output and
never switches the daily image provider. Google currently lists Gemini image
models as having no free API tier; free browser generations in AI Studio do not
guarantee API access. A quota error on a billing-disabled project is an expected
result. The flag is your confirmation, not an API-enforced spending limit: there
is no per-request free-only switch. Do not use a billed project's key for this
probe. See [Google pricing](https://ai.google.dev/gemini-api/docs/pricing).

The bot can maintain one silent, live progress card per cycle: current stage,
real stage progress where available, image count, elapsed time and last update.
`reel-bot-progress.service` refreshes it about every 20 seconds independently of
the heavy worker, including during narration, captions and rendering. It shows
waiting, retry, interruption, stopped and complete states. Finished cards stop
updating, and reruns reuse the same card. This needs Relay's authenticated text
editing API (`PATCH /v1/messages/{id}/text` and the `edit_text` channel capability).
Until that Relay release is deployed, the observer waits without sending cards.

The live progress card follows new steps at the bottom of the bot chat. Older
progress cards are deleted only after their replacement is delivered; percentages
edit the latest card. Relay must support `delete_text` to enable this behavior.
