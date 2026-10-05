#!/usr/bin/env bash
# Fails when a hand-written Python file is too long (see CLAUDE.md "Code size limits").
# skills/relay-notify is copied unchanged from relay, so it is exempt.
set -euo pipefail

MAX_PY="${MAX_PY:-300}"
MAX_TEST="${MAX_TEST:-500}"

cd "$(git rev-parse --show-toplevel)"

status=0
while IFS= read -r -d '' file; do
	[[ "$file" == skills/relay-notify/* ]] && continue
	limit="$MAX_PY"
	[[ "$(basename "$file")" == test_*.py ]] && limit="$MAX_TEST"
	lines=$(wc -l <"$file")
	if ((lines > limit)); then
		echo "$file: $lines lines (limit $limit). Split it by responsibility." >&2
		status=1
	fi
done < <(git ls-files -z --cached --others --exclude-standard -- '*.py')

exit "$status"
