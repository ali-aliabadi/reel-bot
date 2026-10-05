#!/usr/bin/env bash
# Installs the pinned gitleaks release into bin/tools, checking its SHA-256.
# Usage: scripts/install-gitleaks.sh <version> <dest-dir>
set -euo pipefail

version="$1"
dest="$2"

case "$(uname -s)-$(uname -m)" in
Linux-x86_64) platform=linux_x64 sha=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
Darwin-arm64) platform=darwin_arm64 sha=b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
*)
	echo "install-gitleaks: no pinned checksum for $(uname -s)-$(uname -m); install gitleaks yourself" >&2
	exit 1
	;;
esac

tarball="gitleaks_${version#v}_${platform}.tar.gz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/$tarball" \
	"https://github.com/gitleaks/gitleaks/releases/download/${version}/${tarball}"
echo "$sha  $tmp/$tarball" | sha256sum -c - >/dev/null 2>&1 ||
	echo "$sha  $tmp/$tarball" | shasum -a 256 -c - >/dev/null
tar -xzf "$tmp/$tarball" -C "$tmp" gitleaks
install -d "$dest"
install -m 755 "$tmp/gitleaks" "$dest/gitleaks"
