---
name: reel-bot-review
description: Security, content-policy and cost review for reel-bot changes. Use before opening a PR that touches publishing, prompts, asset sourcing, Relay messages, secrets, logging, config or workflows, or when asked for a security or policy check.
---

# reel-bot security, policy and cost review

reel-bot spends money on APIs, holds several API keys, and publishes to platforms
that demonetize or strike channels for breaking their rules. Review the diff
(`git diff master...HEAD`) against this checklist, then the files it touches in
full. Report findings as a list with `file:line`, severity
(critical / high / medium / low) and the fix. Fix critical and high before the PR.

Also run: `make sec` (pip-audit, gitleaks) and `make lint` (includes ruff's bandit rules).

## 1. Human pick and platform policy

- [ ] No code path reaches `publish` (or marks a video ready) without a recorded angle choice: who, when, which option.
- [ ] A missing or late answer skips the video; nothing picks a default on ali's behalf.
- [ ] Videos vary between days inside an arm (hook, visuals, voice pacing): no fixed template that would read as "inauthentic content".
- [ ] Realistic AI imagery or voice sets the platform's AI-content label when posting.
- [ ] Arm B and C scripts keep a source URL for every factual claim; nothing invents quotes or numbers.

## 2. Assets and licences

- [ ] Every music, footage, image and font asset has a recorded source and a licence that allows commercial reuse.
- [ ] No asset is fetched from a URL that came from model output without checking its host against an allowlist.
- [ ] Screenshots (arm C) show only the public page, never ali's accounts, tabs or notifications.

## 3. Secrets and private data

- [ ] No API key, token or `.env` value in code, tests, fixtures, snapshots, `.env.example` or workflow YAML.
- [ ] No log line contains API keys, Relay message bodies or answers, or full model prompts with keys in them. Log video IDs.
- [ ] Relay messages carry no secrets and only what ali needs (see `skills/relay-notify/SKILL.md`, Privacy).
- [ ] A typed Relay answer is validated before use and never executed or spliced into a shell command or SQL.

## 4. Cost and runaway work

- [ ] Every paid API call (model, TTS, image) has a timeout, bounded retries and logs its cost to the video row.
- [ ] Reruns reuse cached stage output instead of paying again.
- [ ] Loops over angles, beats or videos have a hard upper bound; a daily run cannot exceed its arm count.

## 5. Inputs and external processes

- [ ] ffmpeg/ffprobe are called with an argument list (`subprocess.run([...])`), never `shell=True` or a string built from model output.
- [ ] File paths are built under `REEL_BOT_OUT_DIR` from IDs, never from model output or answer text.
- [ ] SQL uses parameters; no string-built queries.
- [ ] Outbound HTTP uses timeouts; provider URLs come from code or config, never from model output.

## 6. CI and supply chain

- [ ] Workflows: actions pinned by SHA, least-privilege `permissions:`, no secrets in logs, no `pull_request_target` with checkout of PR code.
- [ ] New dependencies are justified in the PR, maintained, locked in `uv.lock`, and pass `pip-audit`.
- [ ] Tests never call YouTube, TikTok, Relay or a paid API.
