# reel-bot architecture

This is the design reference for reel-bot. CLAUDE.md summarizes it for agents;
docs/ROADMAP.md tracks what is built. When the design changes, update this file
in the same PR.

## What reel-bot is

A Python CLI that turns an old fable into a finished vertical video for one
adult-framed fables channel (Kalilah and Dimnah, Panchatantra, Rumi, Aesop),
narrated by Tara. ali picks the story; Tara records the narration; reel-bot does
the rest, up to posting on YouTube, Instagram and TikTok. It runs on ali's VPS (next to Relay; later on a schedule), keeps a log of every video
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
| voice | script, Tara's recording | `voice.wav`: 48 kHz mono, rumble and hiss removed, silence trimmed at both ends, loudness -14 LUFS | ffmpeg in two passes (clean and trim, then loudness: in one chain ffmpeg 6.1 hangs on a take with no silence to trim); a take outside the length's expected range (short 35-60 s, long 55-100 s) imports with a warning; a new take replaces the old |
| captions | script, voice | `captions.json`: each script word with its time, short caption cues (up to 3 words or 16 characters, split at punctuation and pauses), and when each part (hook, paragraphs, lesson) is spoken | OpenAI transcription (`whisper-1`, word timestamps, about $0.006 a minute); the script's words are kept and take their times from the matching heard words, unmatched ones are spread across the gap; under 50% matched is refused as the wrong take, under 85% warns; the render stage burns the cues in |
| visuals | script | `picture-<n>.png` per paragraph (1024x1536) and `visuals.json` (section, file, prompt) | OpenAI image API (`gpt-image-1`, medium quality, about $0.06 a picture); the prompt is the paragraph's scene plus the fixed style and a calm lower third for captions; the hook plays over the first picture and the lesson over the last; can run while Tara reads; a rerun after a failure draws only missing pictures; each picture is an `assets` row (source, licence) |
| render | voice, captions, visuals | `render.mp4`: 1080x1920 H.264 (CRF 20) with AAC audio, 30 fps; `captions.ass` | ffmpeg at low priority (`nice`) on 2 threads; each picture pans slowly (alternately down and up) over a frame scaled 10% larger; cuts sit halfway through the pause between parts; the hook plays over the first picture and the lesson over the last; captions burned in with libass in DejaVu Sans Bold (white, black outline, 420 px above the bottom, clear of the Shorts buttons); the output is checked with ffprobe (size, audio, length within 0.5 s of the take); the font is recorded in `assets` |
| review | render | the full MP4 in Relay, to Tara and ali | `reel-bot daily tick` sends it as a file (up to 45 MB, under Telegram's 50 MB bot upload); Tara uploads it to YouTube and Instagram by hand. Needs Relay to accept files that big (its cap was 5 MB) |
| publish | render, script, ali's pick | one post per platform (`posts` table) | `reel-bot publish <video id>` (or `make --publish`): refused without a recorded pick, in code and by a database trigger. YouTube: resumable upload, `containsSyntheticMedia`, locked private until Google's audit. Instagram Reels: Instagram API with Instagram Login from a development-mode Meta app (ali as tester; public, no App Review), upload from disk, AI disclosure in the caption (the API has no label). TikTok: Direct Post, `is_aigc`, `SELF_ONLY` until TikTok's audit. Title is the story's title plus `#shorts`; the description is the hook, the source, "Narrated by Tara; the pictures are made with AI" and tags. A platform that has the video is skipped, so a rerun posts only where it failed; calls that create a post are never retried |

## Data

SQLite at `./data/reel-bot.db` (`REEL_BOT_DB_PATH`). Tables, added by roadmap items:

- `stories`: the catalog (id, tale, title, collection, source, source_ref), synced from `src/reel_bot/data/stories.json`.
- `tales_used`: one row per tale ever told (tale is the primary key: the no-repeat rule), with the story, video ID and time.
- `videos`: id (`vid_` + ULID), created_at, framing (`fable` or `lesson`), length (`short` or `long`), story_options (JSON list offered to ali), story_id, chosen_by, chosen_at. A CHECK makes the pick all or nothing; choosing also spends the tale in `tales_used`, in one transaction, and must be one of the options when options were offered. Where it was posted is in `posts`.
- `video_stages`: one row per (video, stage): output path relative to `out/<video id>/`, cost_usd, seconds, finished_at. A rerun reuses the output instead of paying again.
- `assets`: video id, path (relative to `out/<video id>/`), kind (image, music, footage, font), source, licence, added_at.
- `spend`: one row per paid API call (at, video id, what, cost_usd), written as soon as the call returns; the monthly cap reads it.
- `posts`: video id, platform (`youtube`, `instagram`, `tiktok`), the platform's ID, posted_at; one row per platform; a trigger refuses a row for a video with no recorded pick.
- `daily_steps`: video id, step (`pick`, `take`, `sent`, `failed`), the Relay message ID, at; one row per step, so the daily run never asks or sends twice.
- `metrics`: video id, read_at, views, average percentage viewed, subscribers gained, viewed vs swiped (entered by hand from Studio).

`reel-bot stories` lists the stories whose tale is still unused, `reel-bot stories pick` suggests 3 from different tales, and `reel-bot stories use <story id> <video id>` records a tale as told (refused if it already was). Record the hand-made videos this way too.

`reel-bot video new --framing fable --length short` creates a video and prints its ID, `reel-bot video choose <video id> <story id> --by ali` records the human pick, and `reel-bot video show <video id>` prints the settings, the pick and each finished stage.

`reel-bot script <video id>` writes `out/<video id>/script.json` once the story is picked; a rerun reuses it. Word budgets: short 95-120 spoken words, long 150-200 (Tara reads about 2.3 words a second).

`reel-bot make <video id>` runs every stage the video is ready for and prints each stage's cost and time: with a picked story it writes the script (also as `read-this.txt` for Tara) and draws the pictures, then stops waiting for Tara's take; `reel-bot make <video id> --voice <file>` imports the take (a new take redoes captions and render) and finishes with `render.mp4`. Cached stages are reused and need no API key.

`reel-bot auth youtube|instagram|tiktok` signs in once and saves the sign-in to `tokens.json` next to the database (owner-only, never printed). YouTube and TikTok print a sign-in address; after approving, paste the address the browser lands on (the page may not load; the code is in the address). Instagram asks for the token from the Meta app dashboard and refreshes it on each post once it is a day old. `reel-bot publish <video id> [--to youtube instagram tiktok]` posts to the named platforms, by default every signed-in one.

`reel-bot render <video id>` writes `out/<video id>/render.mp4` once voice, captions and visuals are done, and lists whichever is missing; a rerun reuses it.

`reel-bot visuals <video id>` draws `out/<video id>/picture-<n>.png` once the script exists; a rerun reuses them.

`reel-bot captions <video id>` writes `out/<video id>/captions.json` once the take is imported; a rerun reuses it.

`reel-bot voice <video id> <recording>` cleans Tara's take into `out/<video id>/voice.wav` once the script exists. The Relay flow (phase 3) will call the same import with the voice note it downloads.

## Daily run

Two systemd timers on the VPS drive the day (`deploy/systemd`, `deploy/README.md`):

- `reel-bot daily start --framing F --length L` (08:00) makes a video, offers 3 stories from different untold tales (`story_options`) and asks `RELAY_USER` (ali) to tap one. While an earlier pick is unanswered it sends nothing: there is never a default pick.
- `reel-bot daily tick` (every 5 minutes) moves each open video one step: it records the tapped story as the pick (who is the Relay username that answered, checked against the offered buttons), runs script and visuals, sends the script to `REEL_BOT_NARRATOR` (Tara) as a voice-note question, saves her answer as `take.<ext>`, runs voice, captions and render, and sends the MP4 to Tara and ali.

Each step's Relay message ID is a `daily_steps` row and every message has an idempotency key from the video and step, so a tick can be rerun and never asks or sends twice. Relay being unreachable while polling is logged and retried on the next tick. Any other error stops the video (`failed` step) and sends ali a high-urgency alert with the step and the error; `reel-bot daily retry <video id>` reopens it. The tick timer runs under `MemoryMax` and `Nice` so a bad render can't starve Relay.

Each video's stage outputs go to `./out/<video id>/` (`videos.video_dir`, which accepts only a valid video ID), git-ignored.

## Configuration

| Variable | Required | Default | What |
| --- | --- | --- | --- |
| `RELAY_URL` | yes | | Relay's base URL: `https`, or `http://127.0.0.1:<port>` on Relay's own host (the VPS uses `http://127.0.0.1:18080`, so the videos skip nginx's and Cloudflare's body limits) |
| `RELAY_API_KEY` | yes | | reel-bot's Relay key (`relay clients create reel-bot`) |
| `RELAY_APP` | no | `reel-bot` | Sent as every message's `source` |
| `RELAY_USER` | no | `admin` | Who gets the messages and picks the daily story (ali) |
| `REEL_BOT_NARRATOR` | for `daily` | | Relay username of who reads the script and gets the finished video (Tara) |
| `REEL_BOT_DB_PATH` | no | `./data/reel-bot.db` | SQLite file |
| `REEL_BOT_OUT_DIR` | no | `./out` | Render output |
| `ANTHROPIC_API_KEY` | for `script` | | Claude API key; read by the SDK, never logged |
| `OPENAI_API_KEY` | for `captions`, `visuals` | | OpenAI API key (transcription and pictures); sent only in the request header, never logged |
| `REEL_BOT_MONTHLY_CAP_USD` | no | `20` | Most the paid APIs (Claude, OpenAI) may cost per calendar month, UTC. A call whose worst-case cost would pass it is refused before it is made; a second fence behind the providers' own limits |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | for YouTube | | Google Cloud OAuth client ("Desktop app"); the secret is never logged |
| `YOUTUBE_PRIVACY` | no | `public` | `public`, `unlisted` or `private` (YouTube forces private until the audit) |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` | for TikTok | | The TikTok app's keys and its registered https redirect URI |
| `TIKTOK_PRIVACY` | no | `SELF_ONLY` | `PUBLIC_TO_EVERYONE` once TikTok has audited the app |
| `REEL_BOT_LOG_LEVEL` | no | `info` | `debug`, `info`, `warning` or `error`; JSON lines on stderr, keys and Relay bodies redacted |

`reel-bot config` (or `make run ARGS=config`) checks these and prints them without the key. Platform sign-ins are not env vars: `reel-bot auth` saves them to `tokens.json`.

## Quality tooling

Everything runs through the Makefile, locally and in CI. Python tools are pinned in
`uv.lock`; gitleaks is pinned by version and checksum in the Makefile and
`scripts/install-gitleaks.sh`.

- **ruff** (lint and format) with, beyond the defaults: bugbear, bandit (`S`), pylint, simplify, pyupgrade, `T20` (no `print` outside `cli.py`), complexity 10.
- **pyright** in strict mode.
- **File length:** `scripts/check-file-length.sh` fails on `.py` files over 300 lines (tests 500).
- **Other checks:** `uv lock --check`, `actionlint` for workflows, `pip-audit`, `gitleaks` over the history.
- **CI** (`ci.yml`): `make check` on every PR and push to `master`, with ffmpeg installed so the voice and render smoke tests run; actions pinned by SHA; `contents: read` only.
- **Dependabot:** weekly grouped updates for uv and GitHub Actions.
