# Deploying reel-bot's daily run on the VPS

The daily run is two systemd timers (units in `systemd/`):

- `reel-bot-daily-start.timer` at 08:00 asks ali in Relay to pick one of three stories.
- `reel-bot-daily-tick.timer` every 5 minutes records the pick, makes the script and
  pictures, asks Tara for her voice note, renders it and sends her (and ali) the MP4.

Before installing: the Phase 0 VPS steps in `docs/ROADMAP.md` are done (checkout in
`/opt/reel-bot`, `make tools`, `.env` with the Relay, Anthropic and OpenAI keys and
`REEL_BOT_NARRATOR`), `make run ARGS=config` is clean, and Relay accepts files up to
45 MB (Relay's file cap and nginx's `client_max_body_size`).

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
