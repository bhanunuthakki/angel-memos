#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
fixture_root=$(mktemp -d "${TMPDIR:-/tmp}/angel-public-boundary.XXXXXX")
trap 'rm -rf "$fixture_root"' EXIT
git -C "$fixture_root" init -q
mkdir -p "$fixture_root/scripts"
cp "$repo_root/scripts/check_public_tree.sh" "$fixture_root/scripts/check_public_tree.sh"
printf 'synthetic public fixture\n' > "$fixture_root/README.md"
git -C "$fixture_root" add -f README.md scripts/check_public_tree.sh
(cd "$fixture_root" && bash scripts/check_public_tree.sh)

printf 'api_key=%s\n' 'ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' > "$fixture_root/private.txt"
git -C "$fixture_root" add -f private.txt
if (cd "$fixture_root" && bash scripts/check_public_tree.sh >/dev/null 2>&1); then
  echo "guard accepted credential material" >&2
  exit 1
fi
git -C "$fixture_root" rm -q -f private.txt

printf 'my portfolio cost basis: $1234\n' > "$fixture_root/private.txt"
git -C "$fixture_root" add -f private.txt
if (cd "$fixture_root" && bash scripts/check_public_tree.sh >/dev/null 2>&1); then
  echo "guard accepted a personal account fact" >&2
  exit 1
fi
