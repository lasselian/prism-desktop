#!/usr/bin/env bash
# Regenerate SHA256SUMS for a GitHub release from every asset on it.
#
# The Release workflow writes SHA256SUMS itself, so this is only for repairing
# a release by hand (an asset replaced after publishing, say). Run it after the
# last asset is uploaded. The auto-updater refuses a download that SHA256SUMS
# does not list, so a release whose sums file misses an asset cannot update
# that platform.
#
# Usage: ./update_sha256sums.sh <tag>
set -euo pipefail

tag="${1:?usage: $0 <tag>}"
repo="lasselian/prism-desktop"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mapfile -t assets < <(gh release view "$tag" --repo "$repo" --json assets \
    --jq '.assets[].name | select(. != "SHA256SUMS" and . != "sha256sums.txt")')
if [ "${#assets[@]}" -eq 0 ]; then
    echo "Release $tag has no assets to checksum." >&2
    exit 1
fi

for name in "${assets[@]}"; do
    gh release download "$tag" --repo "$repo" --pattern "$name" --dir "$work"
done

(cd "$work" && sha256sum -- "${assets[@]}" > SHA256SUMS)
cat "$work/SHA256SUMS"
gh release upload "$tag" "$work/SHA256SUMS" --repo "$repo" --clobber
