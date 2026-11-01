#!/usr/bin/env bash
# Runs on the VPS as the deploy key's forced command (deploy/README.md): moves
# /opt/reel-bot to the commit the deploy workflow names and syncs the venv.
#
# The only input is SSH_ORIGINAL_COMMAND, which must be a full commit SHA equal to
# origin/master's head. The key can do nothing else: no shell, no other command.
# It never touches .env, and it needs no sudo: when the systemd units changed it
# says so and exits 3, and ali copies them by hand (deploy/README.md).
#
# Everything is inside main, which bash parses whole before running, so the
# git merge rewriting this file mid-run is harmless.
set -euo pipefail

main() {
	local dir="${REEL_BOT_DIR:-/opt/reel-bot}" sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
	export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin"

	if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
		echo "deploy: expected a full commit SHA" >&2
		return 2
	fi
	cd "$dir"
	if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
		echo "deploy: $dir has local changes; leaving it alone" >&2
		return 1
	fi

	timeout 120 git fetch --quiet origin master
	local head
	head=$(git rev-parse origin/master)
	if [[ "$head" != "$sha" ]]; then
		echo "deploy: origin/master is $head, not $sha; skipping" >&2
		return 0
	fi

	# A tick that is running keeps using the old files; wait for it (30 min cap,
	# the tick unit's own timeout) so it doesn't see half of each version.
	local waited=0
	while systemctl is-active --quiet reel-bot-daily-tick.service; do
		if ((waited >= 1800)); then
			echo "deploy: a daily tick has run for 30 minutes; try again later" >&2
			return 1
		fi
		sleep 10
		waited=$((waited + 10))
	done

	local before
	before=$(git rev-parse HEAD)
	git merge --quiet --ff-only "$sha"
	timeout 900 nice -n 10 uv sync --locked --quiet
	.venv/bin/reel-bot version
	echo "deploy: $before -> $sha"

	local changed=0 unit
	for unit in deploy/systemd/reel-bot-*; do
		if ! cmp -s "$unit" "/etc/systemd/system/$(basename "$unit")"; then
			echo "deploy: $(basename "$unit") differs from the installed copy" >&2
			changed=1
		fi
	done
	if ((changed)); then
		echo "deploy: install the units by hand (deploy/README.md)" >&2
		return 3
	fi
}

main "$@"
exit
