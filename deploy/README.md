# Deploying reel-bot's daily run on the VPS

The daily run is two systemd timers (units in `systemd/`):

- `reel-bot-daily-start.timer` at 08:00 asks ali in Relay to pick one of three stories.
- `reel-bot-daily-tick.timer` every minute records the pick, has Tara get the script
  from ChatGPT, then asks her one step at a time for the voice note and each picture (showing reel-bot's free draft of it first when `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are set), collects her voice note and pictures,
  renders the video and sends her (and ali) the MP4 with its post text to copy.

Before installing: the Phase 0 VPS steps in `docs/ROADMAP.md` are done (checkout in
`/opt/reel-bot`, `make tools`, `make models` for the captions' Whisper model and the reading guide's Kokoro model, `.env` with the Relay key (no Anthropic key needed) and
`REEL_BOT_NARRATOR`), `make run ARGS=config` is clean, and Relay accepts files up to
45 MB and takes picture answers: Relay with the 45 MB file limit and `reply: "image"` questions, `RELAY_MAX_BODY_BYTES=67108864` in `/opt/relay/.env`,
and `RELAY_URL=http://127.0.0.1:18080` in reel-bot's `.env` so the videos skip nginx and
Cloudflare.

```bash
sudo cp /opt/reel-bot/deploy/systemd/reel-bot-daily-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reel-bot-daily-tick.timer reel-bot-daily-start.timer
systemctl list-timers 'reel-bot-*'
journalctl -u reel-bot-daily-tick -n 50     # what the last ticks did
```

An error is retried on the next tick; the tenth in a row (about 10 minutes) stops the video and sends the
picker (`RELAY_USER`) a high-urgency Relay alert naming the video and the step; fix the
cause, then `make run ARGS="daily retry VIDEO_ID"`. To drop a day instead, tap "Skip today" on the
morning pick, or `make run ARGS="daily cancel VIDEO_ID"` (`daily list` shows the IDs). To stop: `sudo systemctl disable --now
reel-bot-daily-start.timer reel-bot-daily-tick.timer`.

## Automatic deploy after a merge

`.github/workflows/deploy.yml` runs after ci passes on a push to master. It SSHes to the
VPS with a key that can only run `scripts/deploy.sh`, passing the commit SHA. The script
refuses anything but origin/master's head, leaves a checkout with local changes alone,
waits for a running tick to finish, then `git merge --ff-only` and `uv sync --locked`. It
never reads `.env` and has no sudo: when a unit in `systemd/` changed it exits 3, the run
goes red, and you copy the units by hand as above. Redeploy by hand with "Run workflow"
on the deploy workflow.

Setup, once, as the user that owns `/opt/reel-bot` on the VPS (its `git pull` must
already work without a prompt):

```bash
ssh-keygen -t ed25519 -N '' -C reel-bot-deploy -f reel-bot-deploy   # on your Mac
# On the VPS, one line in that user's ~/.ssh/authorized_keys:
#   restrict,command="/opt/reel-bot/scripts/deploy.sh" ssh-ed25519 AAAA... reel-bot-deploy
ssh-keyscan -p PORT HOST                                             # for VPS_KNOWN_HOSTS
```

In GitHub, create a `production` environment (Settings, Environments) and set the
secrets `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (the private key file),
`VPS_KNOWN_HOSTS` and, if not 22, `VPS_PORT`. Then delete the private key from your Mac.

## Live progress card

After deploying the reel-bot code, install the independent observer:

```sh
sudo cp /opt/reel-bot/deploy/systemd/reel-bot-progress.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reel-bot-progress.service
journalctl -u reel-bot-progress.service -n 20
```

It uses the existing `.env`, app key and recipients and needs no new secrets.
Relay must first expose text editing and report `edit_text: true` on its Telegram
channel. Until then the observer logs that it is waiting and sends no cards.
Once Relay is upgraded it starts automatically on its next poll. The observer is
capped at 200 MB, does not load AI models, and reads the video database without
writing or migrating it. A diagnostic one-shot update is available with
`python -m reel_bot.cli_progress --once --video-id VIDEO_ID`; specifying an ID
also permits a status card for that completed cycle. The observer can be stopped
with `systemctl stop reel-bot-progress.service` without stopping video generation.
