# Deploying reel-bot's daily run on the VPS

The daily run is two systemd timers (units in `systemd/`):

- `reel-bot-daily-start.timer` at 08:00 asks ali in Relay to pick one of three stories.
- `reel-bot-daily-tick.timer` every 5 minutes records the pick, has Tara get the script
  from ChatGPT, then asks her one step at a time for the voice note and each picture, collects her voice note and pictures,
  renders the video and sends her (and ali) the MP4.

Before installing: the Phase 0 VPS steps in `docs/ROADMAP.md` are done (checkout in
`/opt/reel-bot`, `make tools`, `make models` for the captions' Whisper model, `.env` with the Relay key (no Anthropic key needed) and
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

A failure sends ali a high-urgency Relay alert naming the video and the step; fix the
cause, then `make run ARGS="daily retry VIDEO_ID"`. To stop: `sudo systemctl disable --now
reel-bot-daily-start.timer reel-bot-daily-tick.timer`.
